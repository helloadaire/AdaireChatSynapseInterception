from nio import AsyncClient, MatrixRoom, RoomMessageText
from nio.client import ClientConfig
from typing import Optional, Callable, Dict, Any
import asyncio
import json
import logging
from nio import MegolmEvent, RoomMessage
from nio.crypto import ENCRYPTION_ENABLED
import aiofiles
import pickle
import os

ELEMENT_KEY_PASSPHRASE = 'IWxCmrVzpjSfqicBIu'

from config.settings import settings

logger = logging.getLogger(__name__)


class MatrixClient:
    """Wrapper around matrix-nio client for CRM integration"""

    def __init__(self):
        self.client: Optional[AsyncClient] = None
        self.syncing = False
        self._message_callbacks = []
        self._sync_token = None
        self._store_path = "./matrix_store"

    async def initialize(self):
        """Initialize Matrix client connection with proper configuration"""
        try:
            logger.info(f"🚀 Initializing Matrix client for {settings.matrix_user_id}")

            # Ensure store directory exists
            os.makedirs(self._store_path, exist_ok=True)

            # Create proper ClientConfig object
            config = ClientConfig(
                encryption_enabled=True,  # Enable E2EE
                store_sync_tokens=True,
            )

            # Initialize client WITHOUT store_path initially to avoid issues
            self.client = AsyncClient(
                homeserver=settings.matrix_homeserver_url,
                user=settings.matrix_user_id,
                device_id=settings.matrix_device_id or "CRMBOT",
                config=config,
            )

            # Set access token if provided
            if settings.matrix_access_token:
                self.client.access_token = settings.matrix_access_token
                self.client.user_id = settings.matrix_user_id
                logger.info("✅ Using provided access token")
            else:
                logger.warning("⚠️ No access token provided. Client may not be able to sync.")

            # Add event callbacks
            self.client.add_event_callback(self._on_message, RoomMessageText)
            self.client.add_event_callback(self._on_encrypted, MegolmEvent)

            # IMPORTANT: Initialize encryption BEFORE syncing
            await self._initialize_encryption()

            # Try to import recovery key
            await self._import_recovery_key_if_exists()

            # Log configuration
            logger.info(f"📡 Configured for homeserver: {settings.matrix_homeserver_url}")
            logger.info(f"👤 User ID: {settings.matrix_user_id}")

            # Start syncing
            if self.client.access_token:
                asyncio.create_task(self._start_syncing())
                logger.info("🔄 Starting sync with E2EE...")

            logger.info("✅ Matrix client initialized with E2EE support")

        except Exception as e:
            logger.error(f"❌ Failed to initialize Matrix client: {e}", exc_info=True)
            raise

    async def _import_recovery_key_if_exists(self):
        """Import recovery key from settings if available"""
        try:
            keys_path = os.path.join(self._store_path, 'element-keys.txt')
            logger.info(f"Checking for key at location: {keys_path}")

            if os.path.exists(keys_path):
                # Try to import keys
                result = await self.client.import_keys(keys_path, ELEMENT_KEY_PASSPHRASE)
                logger.info(f"✅ Recovery key imported: {result}")
            else:
                logger.warning(f"⚠️ No key file found at {keys_path}")
        except Exception as e:
            logger.error(f"❌ Error importing recovery key: {e}")

    async def _on_encrypted(self, room: MatrixRoom, event: MegolmEvent):
        """Handle encrypted Megolm events"""
        try:
            logger.info(f"🔐 Received encrypted event from {event.sender} in room {room.room_id}")

            # Try to decrypt
            decrypted = await self.client.decrypt_event(event)

            if decrypted and hasattr(decrypted, 'body'):
                # Process as regular message
                message_data = {
                    "event_id": decrypted.event_id,
                    "room_id": room.room_id,
                    "sender": decrypted.sender,
                    "body": decrypted.body,
                    "message_type": "m.room.message",
                    "timestamp": decrypted.server_timestamp,
                    "room_name": room.display_name or room.room_id,
                    "decrypted": True,
                    "encrypted_event_id": event.event_id,
                }

                logger.info(f"🔓 Decrypted message: {message_data['body'][:100]}")

                # Call callbacks
                for callback in self._message_callbacks:
                    await callback(message_data)
            else:
                logger.warning(f"⚠️ Could not decrypt event from {event.sender}")

        except Exception as e:
            logger.error(f"❌ Error handling encrypted event: {e}")

    async def _initialize_encryption(self):
        """Initialize E2EE encryption store"""
        try:
            logger.info("🔐 Initializing encryption...")

            # Enable encryption on the client
            if not self.client.olm:
                # Initialize OLM if not already done
                await self.client.load_store()
                logger.info("✅ Loaded encryption store")

            # Upload device keys
            if self.client.access_token:
                try:
                    # First make sure we have device keys
                    if not hasattr(self.client, 'device_id') or not self.client.device_id:
                        self.client.device_id = settings.matrix_device_id or "CRMBOT"

                    # Upload keys
                    await self.client.keys_upload()
                    logger.info("✅ Device keys uploaded")

                    # Query keys for ourselves (this doesn't take arguments in newer versions)
                    await self.client.keys_query()
                    logger.info("✅ Queried device keys")

                except Exception as upload_error:
                    logger.warning(f"⚠️ Could not upload device keys: {upload_error}")

            logger.info("🔐 Encryption initialized successfully")

        except Exception as e:
            logger.error(f"❌ Failed to initialize encryption: {e}")
            # Don't raise - we might still be able to operate without full E2EE

    async def _start_syncing(self):
        """Start syncing with Matrix server"""
        self.syncing = True
        logger.info("🔄 Starting Matrix sync loop...")

        # Continuous sync loop
        while self.syncing:
            try:
                # Simple sync with minimal parameters
                sync_response = await self.client.sync(
                    timeout=30000,  # 30 seconds timeout
                    since=self._sync_token,
                    full_state=False  # Don't request full state every time
                )

                if sync_response:
                    # Check if sync_response has required attributes
                    if hasattr(sync_response, 'next_batch'):
                        self._sync_token = sync_response.next_batch
                        logger.debug(f"Updated sync token: {self._sync_token}")
                    else:
                        logger.warning("⚠️ Sync response missing next_batch")

                    # Process joined rooms
                    if hasattr(sync_response, 'rooms') and sync_response.rooms:
                        if hasattr(sync_response.rooms, 'join'):
                            for room_id, room_info in sync_response.rooms.join.items():
                                if hasattr(room_info, 'timeline') and room_info.timeline:
                                    if hasattr(room_info.timeline, 'events'):
                                        await self._process_room_events(room_id, room_info.timeline.events)
                        else:
                            logger.debug("No joined rooms in sync response")
                    else:
                        logger.debug("No rooms data in sync response")

                    # Process to_device events (for E2EE)
                    if hasattr(sync_response, 'to_device') and sync_response.to_device:
                        if hasattr(sync_response.to_device, 'events'):
                            await self._process_to_device_events(sync_response.to_device.events)
                else:
                    logger.warning("⚠️ Empty sync response received")

                # Wait before next sync
                await asyncio.sleep(5)

            except asyncio.CancelledError:
                logger.info("🛑 Sync task cancelled")
                break
            except Exception as e:
                logger.error(f"❌ Matrix sync error: {e}", exc_info=True)
                # Wait longer on error
                await asyncio.sleep(30)

    async def _process_to_device_events(self, events):
        """Process to_device events for E2EE key sharing"""
        try:
            if not events:
                return

            logger.debug(f"🔑 Processing {len(events)} to_device events")

            for event in events:
                try:
                    # Let the client handle to_device events (for key sharing)
                    # The AsyncClient handles to_device events automatically during sync
                    pass
                except Exception as e:
                    logger.warning(f"⚠️ Error processing to_device event: {e}")

        except Exception as e:
            logger.error(f"❌ Error processing to_device events: {e}")

    async def _process_room_events(self, room_id: str, events: list):
        """Process events from a room, handling encryption"""
        for event in events:
            try:
                # Get event type
                event_type = getattr(event, 'type', 'unknown')
                logger.debug(f"Processing event type: {event_type} in room {room_id}")

                # The event callbacks will handle the specific event types
                # We don't need to manually process them here since we've already
                # registered callbacks for RoomMessageText and MegolmEvent

            except Exception as e:
                logger.error(f"❌ Error processing room event: {e}")

    async def _on_message(self, room: MatrixRoom, event: RoomMessageText):
        """Handle incoming Matrix text messages"""
        try:
            # Skip if it's our own message
            if event.sender == self.client.user_id:
                return

            # Create message data structure
            message_data = {
                "event_id": event.event_id,
                "room_id": room.room_id,
                "sender": event.sender,
                "body": event.body,
                "message_type": "m.room.message",
                "timestamp": event.server_timestamp,
                "room_name": room.display_name or room.room_id,
                "msgtype": getattr(event, 'msgtype', 'm.text'),
                "formatted_body": getattr(event, 'formatted_body', None),
                "decrypted": False  # Regular messages aren't encrypted
            }

            # Log the message
            body_preview = message_data['body']
            if len(body_preview) > 100:
                body_preview = body_preview[:100] + "..."

            logger.info(f"📨 Message from {event.sender} in room {room.room_id}")
            logger.info(f"   Content: {body_preview}")

            # Call all registered callbacks
            for callback in self._message_callbacks:
                try:
                    await callback(message_data)
                except Exception as e:
                    logger.error(f"❌ Callback error: {e}")

        except Exception as e:
            logger.error(f"❌ Error processing Matrix message: {e}")

    def add_message_callback(self, callback: Callable):
        """Add callback for incoming messages"""
        self._message_callbacks.append(callback)
        logger.info(f"✅ Added message callback. Total callbacks: {len(self._message_callbacks)}")

    async def send_message(self, room_id: str, message: str, formatted_body: Optional[str] = None):
        """Send message to Matrix room"""
        try:
            content = {
                "msgtype": "m.text",
                "body": message
            }

            if formatted_body:
                content["format"] = "org.matrix.custom.html"
                content["formatted_body"] = formatted_body

            logger.info(f"📤 Sending message to room {room_id}: {message[:50]}...")

            response = await self.client.room_send(
                room_id=room_id,
                message_type="m.room.message",
                content=content
            )

            logger.info(f"✅ Message sent to {room_id}")
            return response

        except Exception as e:
            logger.error(f"❌ Failed to send message to {room_id}: {e}")
            raise

    async def join_room(self, room_id_or_alias: str):
        """Join a Matrix room"""
        try:
            response = await self.client.join(room_id_or_alias)
            logger.info(f"✅ Joined room: {room_id_or_alias}")
            return response
        except Exception as e:
            logger.error(f"❌ Failed to join room {room_id_or_alias}: {e}")
            raise

    async def create_room(self, name: str, invitees: Optional[list] = None, **kwargs):
        """Create a new Matrix room"""
        try:
            response = await self.client.room_create(
                name=name,
                invite=invitees or [],
                **kwargs
            )
            room_id = response.room_id
            logger.info(f"✅ Created room {room_id} with name: {name}")
            return room_id
        except Exception as e:
            logger.error(f"❌ Failed to create room: {e}")
            raise

    async def get_joined_rooms(self):
        """Get list of rooms the client has joined"""
        try:
            response = await self.client.joined_rooms()
            return response.rooms if hasattr(response, 'rooms') else []
        except Exception as e:
            logger.error(f"❌ Failed to get joined rooms: {e}")
            return []

    async def close(self):
        """Close Matrix client connection"""
        logger.info("🛑 Closing Matrix client...")
        self.syncing = False

        if self.client:
            # Save store if needed
            try:
                if hasattr(self.client, 'store') and self.client.store:
                    await self.client.store.save()
                    logger.info("💾 Saved store")
            except Exception as e:
                logger.warning(f"⚠️ Could not save store: {e}")

            await self.client.close()
        logger.info("✅ Matrix client closed")


# Global instance
matrix_client = MatrixClient()