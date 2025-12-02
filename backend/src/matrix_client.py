from nio import AsyncClient, MatrixRoom, RoomMessageText
from nio.client import ClientConfig
from typing import Optional, Callable, Dict, Any
import asyncio
import json
import logging
from nio import MegolmEvent
from nio.crypto import ENCRYPTION_ENABLED
import aiofiles
import pickle
import os

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
            
            self.client = AsyncClient(
                homeserver=settings.matrix_homeserver_url,
                user=settings.matrix_user_id,
                device_id=settings.matrix_device_id or "CRMBOT",
                store_path=self._store_path,
                config=config,
            )
            
            self.client.add_event_callback(self._on_encrypted, MegolmEvent)
            
            # Disable response validation to avoid 'next_batch' errors
            self.client.validate_response = False
            
            # Set access token if provided
            if settings.matrix_access_token:
                self.client.access_token = settings.matrix_access_token
                self.client.user_id = settings.matrix_user_id
                logger.info("✅ Using provided access token")
            else:
                logger.warning("⚠️ No access token provided. Client may not be able to sync.")
            
            # Add event callbacks
            self.client.add_event_callback(self._on_message, RoomMessageText)
            
            # IMPORTANT: Initialize encryption BEFORE syncing
            await self._initialize_encryption()
            
            # IMPORTANT: Import recovery key if available
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
            if hasattr(settings, 'matrix_recovery_key') and settings.matrix_recovery_key:
                recovery_key = settings.matrix_recovery_key.strip()
                
                logger.info("🔑 Importing recovery key...")
                
                # The recovery key is in 4S format (space-separated words)
                # This is a passphrase, not a raw key
                try:
                    # Remove extra spaces and normalize
                    normalized_key = " ".join(recovery_key.split())
                    
                    logger.info(f"Using recovery key (first few words): {normalized_key[:30]}...")
                    
                    # For matrix-nio, we need to use import_keys with the passphrase
                    # The passphrase is the 4S key itself
                    await self.client.import_keys(normalized_key)
                    logger.info("✅ Recovery key imported successfully")
                    
                    # Save recovery key to file for backup
                    crypto_store_path = os.path.join(self._store_path, "crypto")
                    os.makedirs(crypto_store_path, exist_ok=True)
                    
                    recovery_key_path = os.path.join(crypto_store_path, "recovery_key.txt")
                    with open(recovery_key_path, "w") as f:
                        f.write(normalized_key)
                    logger.info("💾 Recovery key saved to file")
                    
                    # After importing keys, we should load the store again
                    try:
                        await self.client.load_store()
                        logger.info("✅ Store reloaded with recovery key")
                    except Exception as load_error:
                        logger.warning(f"⚠️ Could not reload store: {load_error}")
                    
                except Exception as import_error:
                    logger.error(f"❌ Could not import recovery key: {import_error}")
                    
                    # Try alternative: maybe it's a base64 key without spaces
                    try:
                        logger.info("🔄 Trying alternative import method...")
                        
                        # Remove all spaces
                        key_without_spaces = recovery_key.replace(" ", "")
                        
                        # Try as passphrase without spaces
                        await self.client.import_keys(key_without_spaces)
                        logger.info("✅ Recovery key imported (without spaces)")
                    except Exception as alt_error:
                        logger.error(f"❌ Alternative import also failed: {alt_error}")
            else:
                logger.info("ℹ️ No recovery key configured in settings")
                
        except Exception as e:
            logger.error(f"❌ Error importing recovery key: {e}")

    async def import_recovery_key(self, recovery_key: str):
        """Import a recovery key to decrypt historical messages"""
        try:
            # The recovery key is usually a base64-encoded string
            await self.client.import_keys(recovery_key)
            logger.info("✅ Recovery key imported")
            
            # Also save it for future use
            crypto_store_path = os.path.join(self._store_path, "crypto")
            os.makedirs(crypto_store_path, exist_ok=True)
            
            recovery_key_path = os.path.join(crypto_store_path, "recovery_key.txt")
            async with aiofiles.open(recovery_key_path, "w") as f:
                await f.write(recovery_key)
                
        except Exception as e:
            logger.error(f"❌ Failed to import recovery key: {e}")
            raise
    
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

    async def _process_to_device_events(self, events):
        """Process to_device events for E2EE key sharing"""
        try:
            if not events:
                return
                
            logger.info(f"🔑 Processing {len(events)} to_device events")
            
            for event in events:
                try:
                    # Let the client handle to_device events (for key sharing)
                    await self.client.handle_to_device_event(event)
                except Exception as e:
                    logger.warning(f"⚠️ Error processing to_device event: {e}")
                    
        except Exception as e:
            logger.error(f"❌ Error processing to_device events: {e}")
    
    
    async def _initialize_encryption(self):
        """Initialize E2EE encryption store"""
        try:
            # Try to load existing store first
            try:
                # Load the store - this will load or create the crypto store
                await self.client.load_store()
                logger.info("✅ Loaded encryption store")
                
                # Try to upload keys if we have access token
                if self.client.access_token and hasattr(self.client, 'olm'):
                    try:
                        await self.client.keys_upload()
                        logger.info("✅ Device keys uploaded")
                    except Exception as upload_error:
                        logger.warning(f"⚠️ Could not upload device keys: {upload_error}")
                
            except Exception as load_error:
                logger.warning(f"⚠️ Could not load store: {load_error}")
                # Create new store
                await self._create_new_crypto_store()
                
            logger.info("🔐 Encryption initialized successfully")
            
        except Exception as e:
            logger.error(f"❌ Failed to initialize encryption: {e}")
            # Don't raise - we might still be able to operate without full E2EE

    async def _create_new_crypto_store(self):
        """Create a new crypto store"""
        try:
            logger.info("🔑 Creating new encryption keys...")
            
            # Initialize the crypto module
            if hasattr(self.client, 'olm'):
                # Upload initial device keys
                await self.client.keys_upload()
                logger.info("✅ Created new encryption keys")
                
                # If we have access to the homeserver, query our own keys
                if self.client.access_token:
                    # Fix: keys_query doesn't take arguments in newer versions
                    try:
                        await self.client.keys_query()
                        logger.info("✅ Queried device keys")
                    except TypeError:
                        # Older API version
                        await self.client.keys_query({self.client.user_id: []})
                        logger.info("✅ Queried device keys (old API)")
            else:
                logger.warning("⚠️ OLM not available")
                
        except Exception as e:
            logger.error(f"❌ Failed to create crypto store: {e}")
            
            
    async def _handle_encrypted_event(self, room_id: str, event):
        """Handle encrypted MegolmEvent"""
        try:
            logger.info(f"🔐 Encrypted event in room {room_id} from {event.sender}")
            
            # Check if we can decrypt it
            if hasattr(event, 'decrypted') and event.decrypted:
                # Already decrypted
                if hasattr(event, 'body'):
                    await self._process_decrypted_message(room_id, event)
                else:
                    logger.warning(f"⚠️ Event is marked as decrypted but has no body")
            else:
                # Try to decrypt
                try:
                    # The event object already has decryption methods
                    decrypted = await self.client.decrypt_event(event)
                    if decrypted and hasattr(decrypted, 'body'):
                        await self._process_decrypted_message(room_id, decrypted)
                    else:
                        logger.warning(f"⚠️ Could not decrypt event from {event.sender}")
                except Exception as e:
                    logger.warning(f"⚠️ Decryption failed: {e}")
                    
        except Exception as e:
            logger.error(f"❌ Error handling encrypted event: {e}")
            
    async def _handle_message_event(self, room_id: str, event):
        """Handle unencrypted message event"""
        try:
            # Create message data from the event object
            message_data = {
                "event_id": getattr(event, 'event_id', 'unknown'),
                "room_id": room_id,
                "sender": getattr(event, 'sender', 'unknown'),
                "body": getattr(event, 'body', ''),
                "message_type": "m.room.message",
                "timestamp": getattr(event, 'server_timestamp', 0),
                "room_name": room_id,
                "decrypted": False
            }
            
            logger.info(f"📨 Unencrypted message from {message_data['sender']}: {message_data['body'][:100]}")
            
            # Call callbacks
            for callback in self._message_callbacks:
                await callback(message_data)
                
        except Exception as e:
            logger.error(f"❌ Error handling message event: {e}")

    async def _process_decrypted_message(self, room_id: str, decrypted_event):
        """Process a successfully decrypted message"""
        try:
            message_data = {
                "event_id": getattr(decrypted_event, 'event_id', 'unknown'),
                "room_id": room_id,
                "sender": decrypted_event.sender,
                "body": getattr(decrypted_event, 'body', ''),
                "message_type": "m.room.message",
                "timestamp": getattr(decrypted_event, 'server_timestamp', 0),
                "room_name": room_id,
                "decrypted": True
            }
            
            logger.info(f"🔓 Decrypted message from {decrypted_event.sender}: {message_data['body'][:100]}")
            
            # Call callbacks
            for callback in self._message_callbacks:
                await callback(message_data)
                
        except Exception as e:
            logger.error(f"❌ Error processing decrypted message: {e}")
    
    async def _process_room_events(self, room_id: str, events: list):
        """Process events from a room, handling encryption"""
        for event in events:
            try:
                # Get event type - events are objects, not dicts
                if hasattr(event, 'type'):
                    event_type = event.type
                elif hasattr(event, 'source'):
                    # Some events have source as JSON string
                    try:
                        source_dict = event.source
                        event_type = source_dict.get('type', 'unknown')
                    except:
                        event_type = 'unknown'
                else:
                    event_type = 'unknown'
                    
                logger.debug(f"Processing event type: {event_type} in room {room_id}")
                
                if event_type == 'm.room.encrypted':
                    # Handle encrypted event
                    await self._handle_encrypted_event(room_id, event)
                elif event_type == 'm.room.message':
                    # Handle unencrypted message
                    await self._handle_message_event(room_id, event)
                else:
                    # Ignore other event types
                    logger.debug(f"Ignoring event type: {event_type}")
                    
            except Exception as e:
                logger.error(f"❌ Error processing room event: {e}")
    
    async def _try_decrypt_event(self, room_id: str, event: dict):
        """Attempt to decrypt an encrypted event"""
        try:
            # Use client's decrypt method
            decrypted = await self.client.decrypt_event(event)
            return decrypted
        except Exception as e:
            logger.warning(f"⚠️ Decryption failed for event in {room_id}: {e}")
            return None
    
    async def _handle_decrypted_message(self, room_id: str, decrypted_event):
        """Handle a decrypted message"""
        try:
            # Create mock room object
            class MockRoom:
                def __init__(self, room_id):
                    self.room_id = room_id
                    self.display_name = room_id
            
            mock_room = MockRoom(room_id)
            
            # Create message data
            message_data = {
                "event_id": getattr(decrypted_event, 'event_id', 'unknown'),
                "room_id": room_id,
                "sender": decrypted_event.sender,
                "body": getattr(decrypted_event, 'body', ''),
                "message_type": "m.room.message",
                "timestamp": getattr(decrypted_event, 'server_timestamp', 0),
                "room_name": room_id,
                "decrypted": True
            }
            
            logger.info(f"🔓 Decrypted message from {decrypted_event.sender}: {message_data['body'][:100]}")
            
            # Call callbacks
            for callback in self._message_callbacks:
                await callback(message_data)
                
        except Exception as e:
            logger.error(f"❌ Error handling decrypted message: {e}")
    
    async def login_and_initialize(self):
        """Login and initialize encryption"""
        try:
            # First ensure we're logged in
            if not self.client.access_token:
                logger.error("❌ No access token available")
                return False
                
            # Now initialize encryption
            await self._initialize_encryption()
            
            # Start syncing
            asyncio.create_task(self._start_syncing())
            
            logger.info("✅ Logged in and encryption initialized")
            return True
            
        except Exception as e:
            logger.error(f"❌ Failed to login and initialize: {e}")
            return False
    
    async def _start_syncing(self):
        """Start syncing with Matrix server"""
        self.syncing = True
        logger.info("🔄 Starting Matrix sync loop...")
        
        # Continuous sync loop
        while self.syncing:
            try:
                # Simple sync without complex parameters
                sync_response = await self.client.sync(
                    timeout=30000,  # 30 seconds timeout
                    since=self._sync_token,
                    full_state=False  # Don't request full state every time
                )
                
                # Check if sync_response is None or an error
                if sync_response is None:
                    logger.warning("⚠️ Empty sync response received")
                    await asyncio.sleep(30)
                    continue
                    
                if hasattr(sync_response, 'error'):
                    logger.error(f"❌ Sync error: {sync_response.error}")
                    await asyncio.sleep(30)
                    continue
                    
                if hasattr(sync_response, 'rooms') and hasattr(sync_response.rooms, 'join'):
                    # Update sync token
                    if hasattr(sync_response, 'next_batch'):
                        self._sync_token = sync_response.next_batch
                    
                    # Process joined rooms
                    for room_id, room_info in sync_response.rooms.join.items():
                        await self._process_room_events(room_id, room_info.timeline.events)
                    
                    # Process to_device events (for E2EE)
                    if hasattr(sync_response, 'to_device'):
                        await self._process_to_device_events(sync_response.to_device.events)
                else:
                    logger.warning("⚠️ Sync response missing rooms data")
                
                # Wait before next sync
                await asyncio.sleep(10)
                    
            except asyncio.CancelledError:
                logger.info("🛑 Sync task cancelled")
                break
            except Exception as e:
                logger.error(f"❌ Matrix sync error: {e}", exc_info=True)
                # Wait longer on error
                await asyncio.sleep(30)
    
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
        """Close Matrix client connection and save encryption keys"""
        logger.info("🛑 Closing Matrix client...")
        self.syncing = False
        
        if self.client:
            # Save encryption keys if available
            try:
                if hasattr(self.client, 'save_account'):
                    account_data = await self.client.save_account()
                    crypto_store_path = os.path.join(self._store_path, "crypto")
                    os.makedirs(crypto_store_path, exist_ok=True)
                    
                    async with aiofiles.open(os.path.join(crypto_store_path, "account.pickle"), "wb") as f:
                        await f.write(account_data)
                    logger.info("💾 Saved encryption keys")
            except Exception as e:
                logger.warning(f"⚠️ Could not save encryption keys: {e}")
            
            await self.client.close()
        logger.info("✅ Matrix client closed")

# Global instance
matrix_client = MatrixClient()