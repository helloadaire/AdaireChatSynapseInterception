import xmlrpc.client
import json
from typing import Optional, Dict, Any, List
import logging
from datetime import datetime

from config.settings import settings

logger = logging.getLogger(__name__)

class OdooClient:
    """Odoo XML-RPC/JSON-RPC client"""
    
    def __init__(self):
        self.url = settings.odoo_url
        self.db = settings.odoo_database
        self.username = settings.odoo_username
        self.password = settings.odoo_password
        self.common = None
        self.models = None
        self.uid = None
        
    def authenticate(self):
        """Authenticate with Odoo"""
        try:
            self.common = xmlrpc.client.ServerProxy(f'{self.url}/xmlrpc/2/common')
            self.uid = self.common.authenticate(self.db, self.username, self.password, {})
            
            if self.uid:
                self.models = xmlrpc.client.ServerProxy(f'{self.url}/xmlrpc/2/object')
                logger.info("Odoo authentication successful")
                return True
            else:
                logger.error("Odoo authentication failed")
                return False
                
        except Exception as e:
            logger.error(f"Odoo authentication error: {e}")
            raise
    
    def execute(self, model: str, method: str, *args):
        """Execute Odoo RPC method"""
        if not self.uid:
            self.authenticate()
            
        try:
            return self.models.execute_kw(
                self.db, self.uid, self.password,
                model, method, *args
            )
        except Exception as e:
            logger.error(f"Odoo RPC error: {e}")
            raise
    
    def create_ticket(self, partner_id: int, subject: str, description: str, **kwargs):
        """Create new helpdesk ticket"""
        try:
            ticket_data = {
                'name': subject,
                'description': description,
                'partner_id': partner_id,
                'priority': kwargs.get('priority', '1'),
            }
            
            # Add optional fields
            for field in ['channel_id', 'team_id', 'user_id', 'tag_ids']:
                if field in kwargs:
                    ticket_data[field] = kwargs[field]
            
            ticket_id = self.execute('helpdesk.ticket', 'create', [ticket_data])
            logger.info(f"Created Odoo ticket {ticket_id}")
            return ticket_id
            
        except Exception as e:
            logger.error(f"Failed to create Odoo ticket: {e}")
            raise
    
    def add_ticket_message(self, ticket_id: int, author_id: int, body: str, message_type: str = "comment"):
        """Add message to helpdesk ticket"""
        try:
            # First, post the message
            message_data = {
                'body': body,
                'model': 'helpdesk.ticket',
                'res_id': ticket_id,
                'message_type': message_type,
                'author_id': author_id,
            }
            
            message_id = self.execute('mail.message', 'create', [message_data])
            
            # Then, post as a note on the ticket
            note_data = {
                'body': body,
                'ticket_id': ticket_id,
            }
            
            note_id = self.execute('helpdesk.ticket.message', 'create', [note_data])
            
            logger.info(f"Added message to ticket {ticket_id}")
            return message_id, note_id
            
        except Exception as e:
            logger.error(f"Failed to add message to ticket: {e}")
            raise
    
    def search_tickets(self, domain: List, fields: Optional[List] = None):
        """Search for tickets"""
        try:
            if fields is None:
                fields = ['id', 'name', 'partner_id', 'create_date', 'priority']
            
            return self.execute('helpdesk.ticket', 'search_read', [domain], {'fields': fields})
            
        except Exception as e:
            logger.error(f"Failed to search tickets: {e}")
            raise
    
    def find_or_create_partner(self, email: str, name: str):
        """Find or create partner/customer"""
        try:
            # Search for existing partner
            domain = [('email', '=', email)]
            partners = self.execute('res.partner', 'search', [domain])
            
            if partners:
                return partners[0]
            else:
                # Create new partner
                partner_data = {
                    'name': name,
                    'email': email,
                }
                return self.execute('res.partner', 'create', [partner_data])
                
        except Exception as e:
            logger.error(f"Partner lookup/create failed: {e}")
            raise

# Global instance
odoo_client = OdooClient()