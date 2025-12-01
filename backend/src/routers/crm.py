from fastapi import APIRouter, Request, HTTPException, Header, Depends
from typing import Optional, Dict, Any
import hmac
import hashlib

from config.settings import settings
from src.models.events import OdooMessageEvent, WebhookRequest
from src.services.message_service import message_service
from src.utils.logger import logger  # Changed from get_logger

router = APIRouter()
# logger = get_logger()  # Remove this line

# Rest of the file remains the same...
async def verify_crm_webhook(
    request: Request,
    x_signature: Optional[str] = Header(None)
):
    """Verify CRM webhook signature"""
    if not settings.crm_webhook_secret:
        return True
    
    body = await request.body()
    
    if x_signature:
        expected_sig = hmac.new(
            settings.crm_webhook_secret.encode(),
            body,
            hashlib.sha256
        ).hexdigest()
        
        if not hmac.compare_digest(x_signature, expected_sig):
            raise HTTPException(status_code=401, detail="Invalid signature")
    
    return True

@router.post("/webhook")
async def crm_webhook(
    webhook_request: WebhookRequest,
    verified: bool = Depends(verify_crm_webhook)
):
    """Handle Odoo/CRM webhooks"""
    try:
        logger.info(f"Received CRM webhook: {webhook_request.event_type}")
        
        if webhook_request.event_type == "helpdesk.ticket.message":
            # Process message from Odoo to Matrix
            data = webhook_request.data
            
            result = await message_service.process_odoo_message(
                ticket_id=data.get("ticket_id"),
                message=data.get("body"),
                sender_info=data.get("author")
            )
            
            if result["success"]:
                return {"status": "sent", "event_id": result.get("event_id")}
            else:
                logger.error(f"Failed to send to Matrix: {result.get('error')}")
                raise HTTPException(status_code=500, detail=result.get("error"))
        
        elif webhook_request.event_type == "helpdesk.ticket.created":
            # Handle new ticket creation
            logger.info(f"New ticket created: {webhook_request.data}")
            return {"status": "acknowledged"}
        
        else:
            logger.info(f"Unhandled CRM event: {webhook_request.event_type}")
            return {"status": "unhandled"}
            
    except Exception as e:
        logger.error(f"CRM webhook error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/tickets/{ticket_id}/reply")
async def reply_to_ticket(ticket_id: int, message: str):
    """Manual endpoint to reply to a ticket (useful for testing)"""
    try:
        result = await message_service.process_odoo_message(
            ticket_id=ticket_id,
            message=message,
            sender_info={"name": "API User", "id": 0}
        )
        
        if result["success"]:
            return {"success": True, "event_id": result.get("event_id")}
        else:
            raise HTTPException(status_code=400, detail=result.get("error"))
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))