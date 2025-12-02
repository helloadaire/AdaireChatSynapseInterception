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
        self._device_id = settings.matrix_device_id or "CRMBOT"
        self._initialized = False

    async def initialize(self):
        """Initialize Matrix client connection with proper configuration"""
        try:
            if self._initialized:
                logger.info("🔵 Matrix client already initialized")
                return

            logger.info(f"🔵 Initializing Matrix client for {settings.matrix_user_id}")

            # Ensure store directory exists
            os.makedirs(self._store_path, exist_ok=True)

            # Create store directory structure
            crypto_store_path = os.path.join(self._store_path, "crypto")
            os.makedirs(crypto_store_path, exist_ok=True)

            # Create proper ClientConfig object
            config = ClientConfig(
                encryption_enabled=True,  # Enable E2EE
                store_sync_tokens=True,
            )

            # Initialize client
            try:
                self.client = AsyncClient(
                    homeserver=settings.matrix_homeserver_url,
                    user=settings.matrix_user_id,
                    device_id=self._device_id,
                    store_path=self._store_path,
                    config=config,
                )
                logger.info("🟢 AsyncClient created successfully")
            except Exception as e:
                logger.error(f"🔴 Failed to create AsyncClient: {e}")
                raise

            # Disable response validation to avoid 'next_batch' errors
            self.client.validate_response = False

            # Set access token if provided
            if settings.matrix_access_token:
                self.client.access_token = settings.matrix_access_token
                self.client.user_id = settings.matrix_user_id
                logger.info("🟢 Using provided access token")
            else:
                logger.warning("🟡 No access token provided. Client may not be able to sync.")

            # Load store if client was created successfully
            if self.client:
                try:
                    await self.client.load_store()
                    logger.info("🟢 Store loaded successfully")
                except Exception as e:
                    logger.error(f"🔴 Failed to load store: {e}")
                    raise
            else:
                logger.error("🔴 Client not created, cannot load store")
                raise RuntimeError("Client not created")

            # Add event callbacks
            self.client.add_event_callback(self._on_message, RoomMessageText)
            self.client.add_event_callback(self._on_encrypted, MegolmEvent)

            # Initialize encryption
            await self._initialize_encryption()

            # Try to import recovery key
            await self._import_recovery_key_if_exists()

            # Log configuration
            logger.info(f"🔵 Configured for homeserver: {settings.matrix_homeserver_url}")
            logger.info(f"🔵 User ID: {settings.matrix_user_id}")
            logger.info(f"🔵 Device ID: {self._device_id}")

            # Start syncing
            if self.client and self.client.access_token:
                asyncio.create_task(self._start_syncing())
                logger.info("🔄 Starting sync with E2EE...")

            self._initialized = True
            logger.info("🟢 Matrix client initialized with E2EE support")

        except Exception as e:
            logger.error(f"🔴 Failed to initialize Matrix client: {e}", exc_info=True)
            self._initialized = False
            raise

    async def _import_recovery_key_if_exists(self):
        """Import recovery key from settings if available"""
        try:
            keys_path = os.path.join(self._store_path, 'element-keys.txt')
            logger.info(f"🔵 Checking for key at location: {keys_path}")

            if os.path.exists(keys_path):
                # Check if store is loaded
                if not self.client or not self.client.olm:
                    logger.warning("🟡 OLM not loaded, cannot import keys")
                    return

                # Try to import keys
                try:
                    result = await self.client.import_keys(keys_path, ELEMENT_KEY_PASSPHRASE)
                    logger.info("🟢 Recovery key imported successfully")
                except Exception as import_error:
                    logger.warning(f"🟡 Could not import recovery key: {import_error}")
            else:
                logger.warning(f"🟡 No key file found at {keys_path}")
        except Exception as e:
            logger.error(f"🔴 Error importing recovery key: {e}")

    async def _on_encrypted(self, room: MatrixRoom, event: MegolmEvent):
        """Handle encrypted Megolm events"""
        try:
            logger.info(f"🔵 Received encrypted event from {event.sender} in room {room.room_id}")

            # Check if we can decrypt
            if not self.client or not self.client.olm:
                logger.warning("🟡 OLM not initialized, cannot decrypt")
                return

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

                logger.info(f"🟢 Decrypted message: {message_data['body'][:100]}")

                # Call callbacks
                for callback in self._message_callbacks:
                    await callback(message_data)
            else:
                logger.warning(f"🟡 Could not decrypt event from {event.sender}")

        except Exception as e:
            logger.error(f"🔴 Error handling encrypted event: {e}")

    async def _initialize_encryption(self):
        """Initialize E2EE encryption store"""
        try:
            logger.info("🔵 Initializing encryption...")

            # Check if client and OLM are available
            if not self.client:
                logger.error("🔴 Client not available for encryption initialization")
                return

            if not self.client.olm:
                logger.error("🔴 OLM not available after loading store")
                return

            # Upload device keys
            if self.client.access_token:
                try:
                    # Upload keys
                    await self.client.keys_upload()
                    logger.info("🟢 Device keys uploaded")

                    # Query keys for ourselves
                    await self.client.keys_query()
                    logger.info("🟢 Queried device keys")

                except Exception as upload_error:
                    # This might be normal if keys are already uploaded
                    logger.debug(f"🔵 Device key operation: {upload_error}")

            logger.info("🟢 Encryption initialized successfully")

        except Exception as e:
            logger.error(f"🔴 Failed to initialize encryption: {e}")
            # Don't raise - we might still be able to operate without full E2EE

    async def _start_syncing(self):
        """Start syncing with Matrix server"""
        self.syncing = True
        logger.info("🔄 Starting Matrix sync loop...")

        # Initial sync to get token
        try:
            initial_sync = await self.client.sync(
                timeout=30000,
                full_state=True  # First sync should get full state
            )
            
            if initial_sync and hasattr(initial_sync, 'next_batch'):
                self._sync_token = initial_sync.next_batch
                logger.info(f"🔵 Initial sync token: {self._sync_token}")
            else:
                logger.warning("🟡 No next_batch in initial sync")
        except Exception as e:
            logger.error(f"🔴 Initial sync failed: {e}")

        # Continuous sync loop
        while self.syncing and self.client:
            try:
                # Simple sync with minimal parameters
                sync_response = await self.client.sync(
                    timeout=30000,  # 30 seconds timeout
                    since=self._sync_token,
                    full_state=False  # Don't request full state every time
                )

                if sync_response:
                    # Update sync token
                    if hasattr(sync_response, 'next_batch'):
                        self._sync_token = sync_response.next_batch
                        logger.debug(f"🔵 Updated sync token: {self._sync_token}")
                    else:
                        logger.warning("🟡 Sync response missing next_batch")

                    # Log room activity
                    if hasattr(sync_response, 'rooms'):
                        if hasattr(sync_response.rooms, 'join'):
                            for room_id in sync_response.rooms.join.keys():
                                logger.debug(f"🔵 Active room: {room_id}")
                else:
                    logger.warning("🟡 Empty sync response received")

                # Wait before next sync
                await asyncio.sleep(5)

            except asyncio.CancelledError:
                logger.info("🟡 Sync task cancelled")
                break
            except Exception as e:
                logger.error(f"🔴 Matrix sync error: {e}")
                # Wait longer on error
                await asyncio.sleep(30)

    async def _on_message(self, room: MatrixRoom, event: RoomMessageText):
        """Handle incoming Matrix text messages"""
        try:
            # Skip if it's our own message
            if event.sender == self.client.user_id:
                return

            # Check if this is an encrypted room but message is unencrypted
            if room.encrypted and not hasattr(event, 'decrypted'):
                logger.warning(f"🟡 Unencrypted message in encrypted room from {event.sender}")
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
                "decrypted": getattr(event, 'decrypted', False)
            }

            # Log the message
            body_preview = message_data['body']
            if len(body_preview) > 100:
                body_preview = body_preview[:100] + "..."

            logger.info(f"🔵 Message from {event.sender} in room {room.room_id}")
            logger.info(f"   Content: {body_preview}")
            logger.info(f"   Decrypted: {message_data['decrypted']}")

            # Call all registered callbacks
            for callback in self._message_callbacks:
                try:
                    await callback(message_data)
                except Exception as e:
                    logger.error(f"🔴 Callback error: {e}")

        except Exception as e:
            logger.error(f"🔴 Error processing Matrix message: {e}")

    def add_message_callback(self, callback: Callable):
        """Add callback for incoming messages"""
        self._message_callbacks.append(callback)
        logger.info(f"🟢 Added message callback. Total callbacks: {len(self._message_callbacks)}")

    async def send_message(self, room_id: str, message: str, formatted_body: Optional[str] = None):
        """Send message to Matrix room"""
        try:
            if not self.client:
                logger.error("🔴 Client not initialized")
                raise RuntimeError("Client not initialized")

            content = {
                "msgtype": "m.text",
                "body": message
            }

            if formatted_body:
                content["format"] = "org.matrix.custom.html"
                content["formatted_body"] = formatted_body

            logger.info(f"🔵 Sending message to room {room_id}: {message[:50]}...")

            response = await self.client.room_send(
                room_id=room_id,
                message_type="m.room.message",
                content=content
            )

            logger.info(f"🟢 Message sent to {room_id}")
            return response

        except Exception as e:
            logger.error(f"🔴 Failed to send message to {room_id}: {e}")
            raise

    async def join_room(self, room_id_or_alias: str):
        """Join a Matrix room"""
        try:
            if not self.client:
                logger.error("🔴 Client not initialized")
                raise RuntimeError("Client not initialized")

            response = await self.client.join(room_id_or_alias)
            logger.info(f"🟢 Joined room: {room_id_or_alias}")
            return response
        except Exception as e:
            logger.error(f"🔴 Failed to join room {room_id_or_alias}: {e}")
            raise

    async def create_room(self, name: str, invitees: Optional[list] = None, **kwargs):
        """Create a new Matrix room"""
        try:
            if not self.client:
                logger.error("🔴 Client not initialized")
                raise RuntimeError("Client not initialized")

            response = await self.client.room_create(
                name=name,
                invite=invitees or [],
                **kwargs
            )
            room_id = response.room_id
            logger.info(f"🟢 Created room {room_id} with name: {name}")
            return room_id
        except Exception as e:
            logger.error(f"🔴 Failed to create room: {e}")
            raise

    async def get_joined_rooms(self):
        """Get list of rooms the client has joined"""
        try:
            if not self.client:
                logger.error("🔴 Client not initialized")
                return []

            response = await self.client.joined_rooms()
            return response.rooms if hasattr(response, 'rooms') else []
        except Exception as e:
            logger.error(f"🔴 Failed to get joined rooms: {e}")
            return []

    async def close(self):
        """Close Matrix client connection"""
        logger.info("🔵 Closing Matrix client...")
        self.syncing = False
        self._initialized = False

        if self.client:
            try:
                # Save the store
                if hasattr(self.client, 'store') and self.client.store:
                    # For DefaultStore, we need to use the proper save method
                    try:
                        # Try to save using store's save method if it exists
                        if hasattr(self.client.store, 'save'):
                            await self.client.store.save()
                            logger.info("🟢 Saved store")
                        else:
                            # For DefaultStore, we might need to save differently
                            logger.debug("🔵 Store doesn't have save method, skipping")
                    except Exception as save_error:
                        logger.warning(f"🟡 Could not save store: {save_error}")
                    
                    # Also backup crypto separately
                    if hasattr(self.client, 'olm') and self.client.olm:
                        crypto_store_path = os.path.join(self._store_path, "crypto")
                        os.makedirs(crypto_store_path, exist_ok=True)
                        
                        # Export keys for backup
                        try:
                            export_path = os.path.join(crypto_store_path, "exported_keys.txt")
                            await self.client.export_keys(export_path, ELEMENT_KEY_PASSPHRASE)
                            logger.info(f"🟢 Exported encryption keys to {export_path}")
                        except Exception as export_error:
                            logger.warning(f"🟡 Could not export keys: {export_error}")
            except Exception as e:
                logger.warning(f"🟡 Error during cleanup: {e}")

            await self.client.close()
        logger.info("🟢 Matrix client closed")

    def is_initialized(self) -> bool:
        """Check if the client is initialized and ready"""
        return self._initialized and self.client is not None

    async def check_connection(self) -> bool:
        """Check if the client is connected and can sync"""
        try:
            if not self.client or not self.client.access_token:
                return False
            
            # Try a simple sync to check connection
            response = await self.client.sync(timeout=10000, since=self._sync_token)
            return response is not None
        except Exception:
            return False


# Global instance
matrix_client = MatrixClient()