from pydantic import BaseModel, Field
from typing import Optional, Dict, Any
from datetime import datetime

class MatrixMessageEvent(BaseModel):
    """Matrix message event model"""
    event_id: str = Field(..., description="Unique event ID")
    room_id: str = Field(..., description="Matrix room ID")
    sender: str = Field(..., description="User who sent the message")
    body: str = Field(..., description="Message content")
    message_type: str = Field("m.text", description="Message type")
    timestamp: int = Field(..., description="Event timestamp in milliseconds")
    relates_to: Optional[Dict[str, Any]] = Field(None, description="For threaded messages")
    formatted_body: Optional[str] = Field(None, description="HTML formatted message")
    
    class Config:
        json_schema_extra = {
            "example": {
                "event_id": "$abc123",
                "room_id": "!room:matrix.org",
                "sender": "@user:matrix.org",
                "body": "Hello, I need help!",
                "message_type": "m.text",
                "timestamp": 1672531200000
            }
        }

class OdooTicketEvent(BaseModel):
    """Odoo ticket event model"""
    ticket_id: Optional[int] = Field(None, description="Existing ticket ID")
    partner_id: int = Field(..., description="Odoo partner/customer ID")
    subject: str = Field(..., description="Ticket subject")
    description: str = Field(..., description="Ticket description")
    priority: str = Field("1", description="Priority (0-3)")
    channel_id: Optional[int] = Field(None, description="Support channel ID")
    team_id: Optional[int] = Field(None, description="Support team ID")
    matrix_room_id: Optional[str] = Field(None, description="Linked Matrix room")
    
class OdooMessageEvent(BaseModel):
    """Odoo helpdesk message event"""
    ticket_id: int = Field(..., description="Ticket ID")
    author_id: int = Field(..., description="Odoo user/partner ID")
    body: str = Field(..., description="Message content")
    message_type: str = Field("comment", description="comment|email")
    attachment_ids: Optional[list] = Field(None, description="Attachment IDs")
    
class WebhookRequest(BaseModel):
    """Generic webhook request model"""
    event_type: str
    data: Dict[str, Any]
    signature: Optional[str] = None
    timestamp: datetime = Field(default_factory=datetime.now)