from typing import Dict, Any, Optional
import asyncio
import logging
from datetime import datetime

from src.matrix_client import matrix_client
from src.odoo_client import odoo_client
from src.models.events import MatrixMessageEvent, OdooTicketEvent

logger = logging.getLogger(__name__)

class MessageService:
    """Service for handling message processing between Matrix and Odoo"""
    
    def __init__(self):
        self.room_ticket_mapping = {}  # In production, use Redis
        
    async def process_matrix_message(self, message_data: Dict[str, Any]):
        """Process incoming Matrix message and forward to Odoo"""
        try:
            # Parse the message
            event = MatrixMessageEvent(**message_data)
            
            logger.info(f"Processing Matrix message from {event.sender} in {event.room_id}")
            
            # Check if we already have a ticket for this room
            ticket_id = self.room_ticket_mapping.get(event.room_id)
            
            # Extract user info from sender (in real app, you'd have user mapping)
            user_email = f"{event.sender.split(':')[0][1:]}@matrix.user"
            user_name = event.sender
            
            # Find or create Odoo partner
            partner_id = odoo_client.find_or_create_partner(user_email, user_name)
            
            if not ticket_id:
                # Create new ticket
                ticket_id = odoo_client.create_ticket(
                    partner_id=partner_id,
                    subject=f"Matrix Support - {datetime.now().strftime('%Y-%m-%d %H:%M')}",
                    description=event.body,
                    matrix_room_id=event.room_id
                )
                
                # Store mapping
                self.room_ticket_mapping[event.room_id] = ticket_id
                logger.info(f"Created new Odoo ticket {ticket_id} for room {event.room_id}")
            else:
                # Add message to existing ticket
                odoo_client.add_ticket_message(
                    ticket_id=ticket_id,
                    author_id=partner_id,
                    body=event.body,
                    message_type="comment"
                )
                logger.info(f"Added message to existing ticket {ticket_id}")
            
            return {
                "success": True,
                "ticket_id": ticket_id,
                "room_id": event.room_id
            }
            
        except Exception as e:
            logger.error(f"Failed to process Matrix message: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    async def process_odoo_message(self, ticket_id: int, message: str, sender_info: Dict[str, Any]):
        """Process Odoo message and forward to Matrix"""
        try:
            # In real implementation, you would:
            # 1. Look up Matrix room ID from ticket mapping
            # 2. Send message to that room
            # 3. Format message appropriately
            
            # For now, this is a placeholder
            room_id = None
            for room, tid in self.room_ticket_mapping.items():
                if tid == ticket_id:
                    room_id = room
                    break
            
            if room_id:
                # Send to Matrix
                response = await matrix_client.send_message(
                    room_id=room_id,
                    message=f"Support Agent: {message}"
                )
                
                logger.info(f"Sent Odoo message to Matrix room {room_id}")
                return {
                    "success": True,
                    "event_id": getattr(response, 'event_id', None)
                }
            else:
                logger.warning(f"No Matrix room found for ticket {ticket_id}")
                return {
                    "success": False,
                    "error": "No linked Matrix room found"
                }
                
        except Exception as e:
            logger.error(f"Failed to process Odoo message: {e}")
            return {
                "success": False,
                "error": str(e)
            }

# Global instance
message_service = MessageService()