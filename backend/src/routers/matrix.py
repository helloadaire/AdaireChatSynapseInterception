from fastapi import APIRouter, Request, HTTPException, Header, Depends
from typing import Optional
import hmac
import hashlib
import json

from config.settings import settings
from src.models.events import MatrixMessageEvent, WebhookRequest
from src.services.message_service import message_service
from src.utils.logger import logger  # Changed from get_logger

router = APIRouter()
# logger = get_logger()  # Remove this line, use the imported logger directly

# Rest of the file remains the same...
async def verify_matrix_webhook(
    request: Request,
    x_matrix_signature: Optional[str] = Header(None),
    x_matrix_timestamp: Optional[str] = Header(None)
):
    """Verify Matrix webhook signature"""
    if not settings.matrix_webhook_secret:
        return True  # Skip verification if no secret set
    
    body = await request.body()
    
    # Verify timestamp (prevent replay attacks)
    if x_matrix_timestamp:
        timestamp = int(x_matrix_timestamp)
        # Check if timestamp is within acceptable range
        # Implementation depends on your requirements
    
    # Verify HMAC signature
    if x_matrix_signature:
        expected_sig = hmac.new(
            settings.matrix_webhook_secret.encode(),
            body,
            hashlib.sha256
        ).hexdigest()
        
        if not hmac.compare_digest(x_matrix_signature, expected_sig):
            raise HTTPException(status_code=401, detail="Invalid signature")
    
    return True

@router.post("/webhook")
async def matrix_webhook(
    request: Request,
    webhook_request: WebhookRequest,
    verified: bool = Depends(verify_matrix_webhook)
):
    """Handle Matrix App Service webhooks"""
    try:
        logger.info(f"Received Matrix webhook: {webhook_request.event_type}")
        
        if webhook_request.event_type == "m.room.message":
            # Process the message
            result = await message_service.process_matrix_message(webhook_request.data)
            
            if result["success"]:
                return {
                    "status": "processed",
                    "ticket_id": result.get("ticket_id")
                }
            else:
                logger.error(f"Failed to process message: {result.get('error')}")
                raise HTTPException(status_code=500, detail=result.get("error"))
        
        elif webhook_request.event_type == "m.room.member":
            # Handle room membership changes
            logger.info(f"Room membership event: {webhook_request.data}")
            return {"status": "ignored"}
        
        else:
            logger.info(f"Unhandled event type: {webhook_request.event_type}")
            return {"status": "unhandled"}
            
    except Exception as e:
        logger.error(f"Webhook processing error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/rooms/{room_id}/messages")
async def get_room_messages(room_id: str, limit: int = 50):
    """Get messages from a Matrix room"""
    try:
        # This would require additional implementation
        # For now, return placeholder
        return {
            "room_id": room_id,
            "messages": [],
            "count": 0
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/rooms/{room_id}/send")
async def send_matrix_message(room_id: str, message: str, formatted: Optional[str] = None):
    """Send message to Matrix room"""
    try:
        from src.matrix_client import matrix_client
        response = await matrix_client.send_message(room_id, message, formatted)
        return {
            "success": True,
            "event_id": getattr(response, 'event_id', None)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))