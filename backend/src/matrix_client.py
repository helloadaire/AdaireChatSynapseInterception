from nio import AsyncClient, MatrixRoom, RoomMessageText
from nio.client import ClientConfig
from typing import Optional, Callable, Dict, Any
import asyncio
import json
import logging
from nio import MegolmEvent, RoomMessage, ToDeviceEvent
from nio.crypto import ENCRYPTION_ENABLED
from nio.store import SqliteStore
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
            
            user_id = getattr(settings, 'matrix_user_id', '').strip()
            # FIXED: Use SqliteStore for proper crypto store
            self.client = AsyncClient(
                homeserver=settings.matrix_homeserver_url,
                user=user_id,
                device_id=device_id,
                store_path=self._store_path,
                config=config,
            )
            
            await self.client.load_store()
            await self.client.load_crypto_store()

            # Optional: set pickle key for encrypted keys
            self.client.pickle_key = ELEMENT_KEY_PASSPHRASE
            
            logger.info("🟢 AsyncClient created successfully with SqliteStore")
            
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
            # Add key request callback
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

            # Import recovery key if available
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
                    if hasattr(self.client, 'load_crypto_store'):
                        self.client.load_crypto_store()
                        logger.info("🟢 Crypto store loaded")
                    # For nio with SqliteStore, olm should be available after load_store
                    elif hasattr(self.client, 'olm'):
                        logger.info("🟢 OLM already available")
                except Exception as e:
                    logger.warning(f"🟡 Could not load crypto store: {e}")

            # Upload device keys (required for E2EE)
            if self.client.access_token and self.client.olm:
                try:
                    # First upload our own device keys
                    upload_response = await self.client.keys_upload()
                    if upload_response:
                        logger.info("🟢 Device keys uploaded/verified")
                    else:
                        logger.warning("🟡 Device keys upload may have failed")
                    
                    # Initialize encryption - this is crucial!
                    if hasattr(self.client, 'start_encryption'):
                        await self.client.start_encryption()
                        logger.info("🟢 Encryption started")
                        
                except Exception as e:
                    logger.warning(f"🟡 Key operation warning: {e}")
                    # Check if it's already initialized
                    if "already uploaded" in str(e).lower() or "already exists" in str(e).lower():
                        logger.info("🟢 Device keys already uploaded")
                    else:
                        # Try the deprecated method for compatibility
                        try:
                            await self.client.keys_query()
                            logger.info("🟢 Keys queried (compatibility mode)")
                        except Exception as query_error:
                            logger.warning(f"🟡 Compatibility query also failed: {query_error}")

            logger.info("🟢 Encryption initialized successfully")
            
        except Exception as e:
            logger.error(f"🔴 Failed to initialize encryption: {e}")
            # Don't re-raise - we might operate in degraded mode

    async def _handle_to_device_event(self, event):
        """Handle to-device events (key sharing)"""
        try:
            logger.debug(f"🔵 Received to-device event of type: {event.type}")
            
            if event.type == "m.room_key":
                logger.info("🟢 Received room key event")
                # The client should automatically process this and store the key
                
            elif event.type == "m.forwarded_room_key":
                logger.info("🟢 Received forwarded room key")
                
            elif event.type == "m.room_key_request":
                logger.info(f"🔑 Key request from {event.sender}")
                # Handle key requests if needed
                
        except Exception as e:
            logger.error(f"🔴 Error handling to-device event: {e}")

    async def request_keys_for_event(self, event: MegolmEvent):
        """Request encryption keys for a specific encrypted event"""
        try:
            logger.info(f"🔑 Requesting keys for event from {event.sender}")
            
            # Request room key for this specific event
            response = await self.client.request_room_key(event)
            logger.info(f"🟢 Key request sent for event from {event.sender}")
            return response
            
        except Exception as e:
            logger.error(f"🔴 Failed to request keys: {e}")
            return None

    async def _import_recovery_key_if_exists(self):
        """Import SSSS (secure storage) recovery key and restore megolm backup."""
        try:
            recovery_key = getattr(settings, "matrix_recovery_key", None)
            if not recovery_key:
                logger.warning("🟡 No recovery key provided")
                return

            logger.info("🔵 Unlocking Secret Storage using recovery key...")

            # 1. Unlock secret storage
            ssss = await self.client.crypto.open_backup_v1(recovery_key)
            if not ssss:
                logger.error("🔴 Failed to unlock secure backup (SSSS)")
                return
            
            logger.info("🟢 Secure Secret Storage unlocked")

            # 2. Download remote megolm backup
            backup = await self.client.crypto.get_backup_v1()
            if backup.count == 0:
                logger.warning("🟡 No keys in remote backup")
                return

            logger.info(f"🔵 Downloading {backup.count} backup keys...")

            # 3. Restore keys from backup into store
            imported = await self.client.crypto.restore_backup_v1(ssss)
            logger.info(f"🟢 Imported {imported.total} megolm sessions from backup")

        except Exception as e:
            logger.error(f"🔴 Error importing SSSS recovery key: {e}", exc_info=True)

    async def _on_encrypted(self, room: MatrixRoom, event: MegolmEvent):
        """Handle encrypted Megolm events"""
        try:
            logger.info(f"🔵 Received encrypted event from {event.sender} in room {room.room_id}")
            
            # Try to decrypt first
            try:
                decrypted = await self.client.crypto.decrypt_megolm_event(event)
            except Exception as decrypt_error:
                logger.info(f"🔑 No key stored — trying SSSS/backup restore again: {decrypt_error}")
                await self._import_recovery_key_if_exists()
                decrypted = await self.client.crypto.decrypt_megolm_event(event)

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
            else:
                logger.warning(f"🔴 Failed to decrypt event from {event.sender}")
                # Request keys
                await self._request_missing_keys(event, room)
            
        except Exception as e:
            logger.error(f"🔴 Error handling encrypted event: {e}", exc_info=True)

    async def _request_missing_keys(self, event: MegolmEvent, room: MatrixRoom):
        """Request missing encryption keys"""
        try:
            if not self.client:
                return
                
            logger.info(f"🔑 Requesting missing keys for event from {event.sender}")
            
            # Extract session info
            session_id = getattr(event, 'session_id', None)
            sender_key = getattr(event, 'sender_key', None)
            
            if session_id:
                logger.info(f"   Session ID: {session_id[:20]}...")
            
            # Method 1: Request room key directly
            try:
                await self.client.request_room_key(event)
                logger.info("🟢 Room key requested")
            except Exception as e:
                logger.warning(f"🟡 Direct key request failed: {e}")
                
            # Method 2: Try alternative approach - query keys with correct API
            try:
                # Fix for API compatibility - try different approaches
                user_id = event.sender
                
                # Approach 1: Try without parameters (older API)
                try:
                    query_response = await self.client.keys_query()
                    logger.info("🟢 Keys queried (no parameters)")
                except TypeError:
                    # Approach 2: Try with empty dict (newer API)
                    query_response = await self.client.keys_query({})
                    logger.info("🟢 Keys queried (empty dict)")
                    
                # If we got a response, try to send key request to all devices
                if query_response and hasattr(query_response, 'device_keys'):
                    devices = query_response.device_keys.get(user_id, {})
                    
                    if not devices:
                        # Try to fetch device list separately
                        logger.info(f"🔑 No devices in query response, trying device list for {user_id}")
                        # We'll skip device-specific requests for now
                        return
                        
                    for device_id in devices.keys():
                        logger.info(f"🔑 Sending key request to device {device_id}")
                        
                        # Create a key request
                        request_content = {
                            "action": "request",
                            "requesting_device_id": self.client.device_id,
                            "request_id": f"req_{int(time.time())}",
                            "body": {
                                "algorithm": "m.megolm.v1.aes-sha2",
                                "room_id": room.room_id,
                                "sender_key": sender_key,
                                "session_id": session_id,
                            }
                        }
                        
                        # Send to-device message requesting keys
                        await self.client.to_device(
                            "m.room_key_request",
                            {
                                user_id: {
                                    device_id: request_content
                                }
                            }
                        )
                        logger.info(f"🟢 Key request sent to {device_id}")
                        
            except Exception as e:
                logger.warning(f"🟡 Device key request failed: {e}")
                
        except Exception as e:
            logger.error(f"🔴 Failed to request missing keys: {e}")
    
    async def create_encrypted_room(self, name: str, invitees: Optional[list] = None):
        """Create an encrypted room"""
        try:
            room_id = await self.create_room(
                name=name,
                invitees=invitees,
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
                    }
                ],
                power_level_content_override={
                    "users": {
                        self.client.user_id: 100,
                        **{user: 50 for user in (invitees or [])}
                    }
                }
            )
            
            logger.info(f"🟢 Created encrypted room: {room_id}")
            return room_id
            
        except Exception as e:
            logger.error(f"🔴 Failed to create encrypted room: {e}")
            raise
    
    async def verify_user_device(self, user_id: str, device_id: str):
        """Manually verify a user's device"""
        try:
            logger.info(f"🔐 Verifying device {device_id} for user {user_id}")
            
            # Get device info
            devices = await self.client.query_keys({user_id: []})
            
            if user_id in devices and device_id in devices[user_id]:
                # Manually mark as verified
                await self.client.set_device_verified(user_id, device_id, verified=True)
                logger.info(f"🟢 Device {device_id} verified")
                return True
                
        except Exception as e:
            logger.error(f"🔴 Failed to verify device: {e}")
        
        return False
    
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

    async def _process_room_events(self, room_id: str, events):
        """Process events from a room"""
        try:
            if not events:
                return
                
            logger.debug(f"🔵 Processing {len(events)} events for room {room_id}")
            
            for event in events:
                try:
                    # Log event type
                    event_type = None
                    
                    if hasattr(event, 'type'):
                        event_type = event.type
                    elif isinstance(event, dict) and 'type' in event:
                        event_type = event['type']
                    
                    if event_type:
                        logger.debug(f"🔵 Event type: {event_type} in room {room_id}")
                        
                        # Handle different event types
                        if event_type == 'm.room.encrypted':
                            logger.info(f"🔐 Encrypted event in {room_id}")
                            # The _on_encrypted callback should handle this
                        elif event_type == 'm.room.message':
                            logger.info(f"📨 Message event in {room_id}")
                            # The _on_message callback should handle this
                            
                except Exception as event_error:
                    logger.debug(f"🟡 Error processing event: {event_error}")
                    
        except Exception as e:
            logger.error(f"🔴 Error processing room events: {e}")
    
    async def _start_syncing(self):
        """Start syncing with Matrix server"""
        self.syncing = True
        logger.info("🔄 Starting Matrix sync loop...")

        # Don't do initial sync - just start normal sync
        logger.info("🔵 Starting normal sync loop...")
        
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
                    # Try to get next_batch from different possible locations
                    next_batch = None
                    
                    # Method 1: Direct attribute
                    if hasattr(sync_response, 'next_batch'):
                        next_batch = sync_response.next_batch
                    
                    # Method 2: From dict if response is dict-like
                    elif isinstance(sync_response, dict) and 'next_batch' in sync_response:
                        next_batch = sync_response['next_batch']
                    
                    # Method 3: Try to parse as JSON string
                    elif isinstance(sync_response, str):
                        try:
                            data = json.loads(sync_response)
                            next_batch = data.get('next_batch')
                        except:
                            pass
                    
                    if next_batch:
                        self._sync_token = next_batch
                        logger.debug(f"🔵 Updated sync token: {self._sync_token[:20]}..." if self._sync_token else "None")
                    else:
                        logger.debug("🟡 No next_batch found in sync response")
                    
                    # Try to process rooms if they exist
                    try:
                        # Check for rooms in different formats
                        rooms_data = None
                        
                        if hasattr(sync_response, 'rooms'):
                            rooms_data = sync_response.rooms
                        elif isinstance(sync_response, dict) and 'rooms' in sync_response:
                            rooms_data = sync_response['rooms']
                        
                        if rooms_data:
                            # Process joined rooms
                            join_rooms = None
                            
                            if hasattr(rooms_data, 'join'):
                                join_rooms = rooms_data.join
                            elif isinstance(rooms_data, dict) and 'join' in rooms_data:
                                join_rooms = rooms_data['join']
                            
                            if join_rooms:
                                for room_id, room_info in join_rooms.items():
                                    logger.debug(f"🔵 Processing room: {room_id}")
                                    
                                    # Try to get timeline events
                                    timeline_events = []
                                    
                                    if hasattr(room_info, 'timeline') and hasattr(room_info.timeline, 'events'):
                                        timeline_events = room_info.timeline.events
                                    elif isinstance(room_info, dict) and 'timeline' in room_info:
                                        timeline = room_info['timeline']
                                        if isinstance(timeline, dict) and 'events' in timeline:
                                            timeline_events = timeline['events']
                                    
                                    if timeline_events:
                                        await self._process_room_events(room_id, timeline_events)
                    except Exception as room_error:
                        logger.debug(f"🟡 Error processing rooms: {room_error}")
                        
                else:
                    logger.debug("🟡 Empty sync response received")

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
                    try:
                        if hasattr(self.client.store, 'save'):
                            await self.client.store.save()
                            logger.info("🟢 Saved store")
                        else:
                            logger.debug("🔵 Store doesn't have save method, skipping")
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


# Global instance
matrix_client = MatrixClient()