from nio import AsyncClient, MatrixRoom, RoomMessageText
from nio.client import ClientConfig
from typing import Optional, Callable, Dict, Any
import asyncio
import json
import logging
from nio import MegolmEvent, RoomMessage, ToDeviceEvent
from nio.crypto import ENCRYPTION_ENABLED
import aiofiles
import pickle
import os
import time

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
        self._room_creation_queue = asyncio.Queue()  # For managing room creation
        self._active_dm_rooms = {}  # Track DM rooms we've created

    async def initialize(self):
        """Initialize Matrix client connection with proper configuration"""
        try:
            if self._initialized:
                logger.info("🔵 Matrix client already initialized")
                return

            logger.info(f"🔵 Initializing Matrix client for {settings.matrix_user_id}")

            # Ensure store directory exists
            os.makedirs(self._store_path, exist_ok=True)

            # Create proper ClientConfig object
            config = ClientConfig(
                encryption_enabled=True,
                store_sync_tokens=True,
            )

            # Initialize client with device ID from settings
            device_id = getattr(settings, 'matrix_device_id', None) or "CRMBOT001"
            
            # Initialize AsyncClient
            self.client = AsyncClient(
                homeserver=settings.matrix_homeserver_url,
                user=settings.matrix_user_id,
                device_id=device_id,
                store_path=self._store_path,
                config=config,
            )

            # Set pickle key for encrypted store
            self.client.pickle_key = ELEMENT_KEY_PASSPHRASE.encode()  # Convert to bytes
            
            logger.info("🟢 AsyncClient created successfully")
            
            # IMPORTANT: Set access token BEFORE loading store or initializing encryption
            if settings.matrix_access_token:
                self.client.access_token = settings.matrix_access_token
                self.client.user_id = settings.matrix_user_id
                logger.info("🟢 Using provided access token")
            else:
                logger.error("🔴 Access token required for E2EE")
                raise ValueError("Access token required")

            # Disable response validation (optional but can help with compatibility)
            self.client.validate_response = False

            # Add event callbacks
            self.client.add_event_callback(self._on_message, RoomMessageText)
            self.client.add_event_callback(self._on_encrypted, MegolmEvent)
            self.client.add_event_callback(self._handle_to_device_event, (ToDeviceEvent,))

            # Load store BEFORE initializing encryption
            try:
                # Load the store properly
                await self.client.load_store()
                logger.info("🟢 Store loaded successfully")
            except Exception as e:
                logger.warning(f"🟡 Could not load store: {e}")

            # Initialize encryption PROPERLY
            await self._initialize_encryption_properly()

            # Import recovery key if available (for existing sessions)
            await self._import_recovery_key_if_exists()

            # Log configuration
            logger.info(f"🔵 Configured for homeserver: {settings.matrix_homeserver_url}")
            logger.info(f"🔵 User ID: {settings.matrix_user_id}")
            logger.info(f"🔵 Device ID: {device_id}")

            # Start sync
            if self.client and self.client.access_token:
                asyncio.create_task(self._start_syncing())
                logger.info("🔄 Starting sync with E2EE...")

            self._initialized = True
            logger.info("🟢 Matrix client initialized with E2EE support")

        except Exception as e:
            logger.error(f"🔴 Failed to initialize Matrix client: {e}", exc_info=True)
            self._initialized = False
            raise
        
    async def _initialize_encryption_properly(self):
        """Properly initialize E2EE encryption"""
        try:
            logger.info("🔵 Initializing encryption properly...")

            if not self.client:
                raise RuntimeError("Client not available")

            # Ensure OLM is loaded
            if not self.client.olm:
                logger.info("🔵 Loading OLM...")
                # Load crypto store
                try:
                    if self.client.olm is None:
                        self.client.olm = await self.client._load_olm()
                        logger.info("🟢 OLM loaded successfully")
                except Exception as e:
                    logger.warning(f"🟡 Could not load OLM: {e}")

            # Upload device keys (required for E2EE)
            if self.client.access_token and self.client.olm:
                try:
                    # First upload our own device keys
                    upload_response = await self.client.keys_upload()
                    if upload_response:
                        logger.info("🟢 Device keys uploaded/verified")
                    else:
                        logger.warning("🟡 Device keys upload may have failed")
                    
                    # Initialize encryption
                    if hasattr(self.client, 'start_encryption'):
                        await self.client.start_encryption()
                        logger.info("🟢 Encryption started")
                        
                except Exception as e:
                    logger.warning(f"🟡 Key operation warning: {e}")
                    if "already uploaded" in str(e).lower() or "already exists" in str(e).lower():
                        logger.info("🟢 Device keys already uploaded")

            logger.info("🟢 Encryption initialized successfully")
            
        except Exception as e:
            logger.error(f"🔴 Failed to initialize encryption: {e}")

    async def _handle_to_device_event(self, event):
        """Handle to-device events (key sharing)"""
        try:
            logger.debug(f"🔵 Received to-device event of type: {event.type}")
            
            if event.type == "m.room_key":
                logger.info("🟢 Received room key event")
                # Store the key automatically
                
            elif event.type == "m.forwarded_room_key":
                logger.info("🟢 Received forwarded room key")
                
            elif event.type == "m.room_key_request":
                logger.info(f"🔑 Key request from {event.sender}")
                # Auto-share keys when requested (makes bot more cooperative)
                await self._auto_share_keys(event)
                
        except Exception as e:
            logger.error(f"🔴 Error handling to-device event: {e}")

    async def _auto_share_keys(self, event):
        """Automatically share keys when requested (makes bot cooperative)"""
        try:
            content = getattr(event, 'content', {})
            if content.get('action') == 'request':
                requesting_device = content.get('requesting_device_id')
                sender = event.sender
                
                # Mark the requesting device as verified
                if hasattr(self.client, 'set_device_verified'):
                    await self.client.set_device_verified(sender, requesting_device, verified=True)
                    logger.info(f"🔐 Auto-verified device {requesting_device} from {sender}")
                    
        except Exception as e:
            logger.warning(f"🟡 Auto-share failed: {e}")

    async def _import_recovery_key_if_exists(self):
        """Import recovery key for existing sessions"""
        try:
            recovery_key = ELEMENT_KEY_PASSPHRASE
            logger.info("🔵 Attempting to import recovery key...")

            if not self.client or not self.client.olm:
                logger.error("🔴 OLM not initialized, cannot import keys")
                return

            # Try to import from file if it exists
            keys_path = os.path.join(self._store_path, 'element-keys.txt')
            if os.path.exists(keys_path):
                logger.info(f"🔵 Found key file at {keys_path}")
                try:
                    result = await self.client.import_keys(keys_path, recovery_key)
                    logger.info("🟢 Recovery key imported from file")
                except Exception as e:
                    logger.warning(f"🟡 Could not import recovery key: {e}")
                    # Create new backup instead
                    if hasattr(self.client, 'enable_backup'):
                        await self.client.enable_backup(recovery_key)
                        logger.info("🟢 Created new encryption backup")
            else:
                logger.info("🟡 No key file found, starting fresh")
                
        except Exception as e:
            logger.error(f"🔴 Error importing recovery key: {e}", exc_info=True)

    # ==================== CORE FIX: ROOM MANAGEMENT ====================

    async def create_dm_room(self, user_id: str, room_name: Optional[str] = None):
        """
        Create a DM room with encryption enabled from the start.
        This ensures the bot receives session keys automatically.
        
        Args:
            user_id: Matrix user ID to create DM with
            room_name: Optional room display name
            
        Returns:
            room_id: The created room ID
        """
        try:
            if not room_name:
                # Extract username from Matrix ID
                username = user_id.split(':')[0].lstrip('@')
                room_name = f"DM with {username}"
            
            logger.info(f"🔵 Creating encrypted DM room with {user_id}")
            
            # Create room with encryption enabled from the start
            response = await self.client.room_create(
                name=room_name,
                invite=[user_id],
                is_direct=True,
                initial_state=[
                    {
                        "type": "m.room.encryption",
                        "state_key": "",
                        "content": {
                            "algorithm": "m.megolm.v1.aes-sha2"
                        }
                    },
                    {
                        "type": "m.room.history_visibility",
                        "state_key": "",
                        "content": {
                            "history_visibility": "shared"
                        }
                    },
                    {
                        "type": "m.room.guest_access",
                        "state_key": "",
                        "content": {
                            "guest_access": "forbidden"
                        }
                    }
                ],
                power_level_content_override={
                    "users": {
                        self.client.user_id: 100,
                        user_id: 50
                    },
                    "events": {
                        "m.room.encryption": 100,
                        "m.room.history_visibility": 100,
                        "m.room.name": 50,
                        "m.room.power_levels": 100
                    }
                }
            )
            
            room_id = response.room_id
            self._active_dm_rooms[user_id] = room_id
            
            logger.info(f"🟢 Created encrypted DM room {room_id} with {user_id}")
            
            # Send welcome message
            await self.send_message(room_id, "Hello! I'm Adaire. How can I help you today?")
            
            return room_id
            
        except Exception as e:
            logger.error(f"🔴 Failed to create DM room with {user_id}: {e}")
            raise

    async def get_or_create_dm_room(self, user_id: str):
        """
        Get existing DM room or create a new one.
        This is the recommended way to ensure bot can decrypt messages.
        """
        try:
            # First check if we already have a DM room with this user
            if user_id in self._active_dm_rooms:
                room_id = self._active_dm_rooms[user_id]
                logger.info(f"🔵 Using existing DM room {room_id} with {user_id}")
                return room_id
            
            # Check joined rooms for existing DM
            response = await self.client.joined_rooms()
            if hasattr(response, 'rooms'):
                for room_id in response.rooms:
                    try:
                        # Get room members to check if it's a DM with this user
                        members = await self.client.room_get_state(room_id)
                        # Simplified check - in production you'd want to check m.direct state
                        # For now, create new room if unsure
                        pass
                    except:
                        continue
            
            # Create new DM room
            return await self.create_dm_room(user_id)
            
        except Exception as e:
            logger.error(f"🔴 Failed to get/create DM room: {e}")
            raise

    async def handle_incoming_dm(self, room: MatrixRoom, event):
        """
        Handle incoming DM messages. If we can't decrypt, 
        create a new room and ask user to continue there.
        """
        try:
            # Skip our own messages
            if event.sender == self.client.user_id:
                return
                
            # If this is a DM and we can't decrypt, create new room
            if room.is_group:
                return
                
            # Check if we're in our list of created rooms
            user_id = event.sender
            if user_id not in self._active_dm_rooms:
                logger.warning(f"🟡 Received message in non-managed DM with {user_id}")
                
                # Create new room and redirect conversation
                new_room_id = await self.create_dm_room(user_id)
                await self.send_message(
                    new_room_id,
                    f"I noticed we were chatting in another room. Let's continue here for better encryption support!"
                )
                
        except Exception as e:
            logger.error(f"🔴 Error handling incoming DM: {e}")

    # ==================== ENCRYPTED EVENT HANDLING ====================

    async def _on_encrypted(self, room: MatrixRoom, event: MegolmEvent):
        """Handle encrypted Megolm events"""
        try:
            # Skip our own messages
            if event.sender == self.client.user_id:
                logger.debug("🔵 Skipping encrypted message from ourselves")
                return
                
            logger.info(f"🔵 Received encrypted event from {event.sender} in room {room.room_id}")
            
            # Handle DMs specially
            if not room.is_group:
                await self.handle_incoming_dm(room, event)
            
            # First check if we can decrypt
            if self.client and self.client.olm:
                try:
                    decrypted = await self.client.decrypt_event(event)

                    if decrypted and hasattr(decrypted, 'body'):
                        # Successfully decrypted!
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
                        return
                            
                except Exception as decrypt_error:
                    error_msg = str(decrypt_error)
                    logger.warning(f"🔑 Decryption failed: {error_msg}")
                    
                    # If we can't decrypt and it's a DM, offer to create new room
                    if not room.is_group:
                        user_id = event.sender
                        logger.info(f"🔑 Cannot decrypt DM from {user_id}, offering new room")
                        
                        # Don't spam requests - track failed attempts
                        if not hasattr(self, '_failed_decryption_attempts'):
                            self._failed_decryption_attempts = {}
                        
                        attempts = self._failed_decryption_attempts.get(user_id, 0)
                        if attempts < 2:  # Only offer twice
                            self._failed_decryption_attempts[user_id] = attempts + 1
                            
                            # Create new room
                            new_room_id = await self.create_dm_room(user_id)
                            await self.send_message(
                                new_room_id,
                                "I couldn't decrypt messages in our previous conversation. "
                                "Let's chat here instead where encryption works properly!"
                            )

            logger.debug(f"🔑 No decryption key for event from {event.sender}")
            
        except Exception as e:
            logger.error(f"🔴 Error handling encrypted event: {e}", exc_info=True)

    # ==================== MESSAGE HANDLING ====================

    async def _on_message(self, room: MatrixRoom, event: RoomMessageText):
        """Handle incoming Matrix text messages"""
        try:
            # Skip our own messages
            if event.sender == self.client.user_id:
                return

            # Check if this is an encrypted room but message is unencrypted
            if room.encrypted and not hasattr(event, 'decrypted'):
                logger.warning(f"🟡 Unencrypted message in encrypted room from {event.sender}")
                # Still process it, but warn
                
            # Check if it's a DM
            is_dm = not room.is_group
            
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
                "decrypted": getattr(event, 'decrypted', False),
                "is_direct_message": is_dm
            }

            # Log the message
            body_preview = message_data['body']
            if len(body_preview) > 100:
                body_preview = body_preview[:100] + "..."

            msg_type = "DM" if is_dm else "Room"
            logger.info(f"🔵 {msg_type} message from {event.sender}: {body_preview}")

            # Call all registered callbacks
            for callback in self._message_callbacks:
                try:
                    await callback(message_data)
                except Exception as e:
                    logger.error(f"🔴 Callback error: {e}")

        except Exception as e:
            logger.error(f"🔴 Error processing Matrix message: {e}")

    # ==================== PUBLIC API ====================

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
        """Create a new Matrix room (generic)"""
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
                    try:
                        if hasattr(self.client.store, 'save'):
                            await self.client.store.save()
                            logger.info("🟢 Saved store")
                    except Exception as save_error:
                        logger.warning(f"🟡 Could not save store: {save_error}")
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

    # ==================== SYNC LOOP ====================

    async def _start_syncing(self):
        """Start syncing with Matrix server"""
        self.syncing = True
        logger.info("🔄 Starting Matrix sync loop...")

        # Continuous sync loop
        while self.syncing and self.client:
            try:
                sync_response = await self.client.sync(
                    timeout=30000,
                    since=self._sync_token,
                    full_state=False
                )

                if sync_response:
                    # Update sync token
                    if hasattr(sync_response, 'next_batch'):
                        self._sync_token = sync_response.next_batch
                    elif isinstance(sync_response, dict) and 'next_batch' in sync_response:
                        self._sync_token = sync_response['next_batch']

                # Wait before next sync
                await asyncio.sleep(5)

            except asyncio.CancelledError:
                logger.info("🟡 Sync task cancelled")
                break
            except Exception as e:
                logger.error(f"🔴 Matrix sync error: {e}")
                await asyncio.sleep(30)

    async def _process_room_events(self, room_id: str, events):
        """Process events from a room"""
        try:
            if not events:
                return
                
            logger.debug(f"🔵 Processing {len(events)} events for room {room_id}")
            
            for event in events:
                try:
                    event_type = None
                    
                    if hasattr(event, 'type'):
                        event_type = event.type
                    elif isinstance(event, dict) and 'type' in event:
                        event_type = event['type']
                    
                    if event_type:
                        logger.debug(f"🔵 Event type: {event_type} in room {room_id}")
                            
                except Exception as event_error:
                    logger.debug(f"🟡 Error processing event: {event_error}")
                    
        except Exception as e:
            logger.error(f"🔴 Error processing room events: {e}")


# Global instance
matrix_client = MatrixClient()