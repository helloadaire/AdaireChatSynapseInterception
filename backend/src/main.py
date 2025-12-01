from fastapi import FastAPI, Request, Depends, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, HTMLResponse
import uvicorn
from typing import List, Dict, Any
import json
from fastapi.responses import HTMLResponse
from pathlib import Path
import asyncio

from config.settings import settings
from src.routers import matrix, crm
from src.utils.logger import logger

app = FastAPI(
    title=settings.app_name,
    version="1.0.0",
    docs_url="/docs" if settings.debug else None,
    redoc_url="/redoc" if settings.debug else None,
)

# Store recent messages for testing
recent_messages: List[Dict[str, Any]] = []
all_events: List[Dict[str, Any]] = []

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure properly in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global exception handler
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Global exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"message": "Internal server error", "detail": str(exc) if settings.debug else "An error occurred"}
    )

# Health check endpoint
@app.get("/health")
async def health_check():
    """Basic health check"""
    return {
        "status": "healthy",
        "service": settings.app_name,
        "version": app.version,
        "matrix_connected": True,
        "message_count": len(recent_messages)
    }

@app.get("/matrix/status")
async def matrix_status():
    """Check Matrix connection status"""
    try:
        from src.matrix_client import matrix_client
        if matrix_client.client:
            # Try to get user info
            whoami = await matrix_client.client.whoami()
            return {
                "status": "connected",
                "user_id": whoami.user_id,
                "device_id": matrix_client.client.device_id,
                "syncing": matrix_client.syncing,
                "joined_rooms": len(matrix_client.client.rooms),
                "next_batch": matrix_client._sync_token
            }
        else:
            return {"status": "not_initialized"}
    except Exception as e:
        return {"status": "error", "detail": str(e)}

# Test endpoint to send a message
@app.post("/test/send")
async def test_send(room_id: str, message: str):
    """Test endpoint to send a message"""
    try:
        from src.matrix_client import matrix_client
        response = await matrix_client.send_message(room_id, message)
        return {
            "success": True,
            "event_id": getattr(response, 'event_id', None),
            "room_id": room_id
        }
    except Exception as e:
        return {"success": False, "error": str(e)}

# View recent intercepted messages
@app.get("/messages")
async def get_messages(limit: int = 20):
    """Get recent intercepted messages"""
    return {
        "count": len(recent_messages),
        "messages": recent_messages[-limit:]
    }

# Simple web interface to see messages
@app.get("/monitor", response_class=HTMLResponse)
async def monitor():
    file_path = Path("src/views/monitoring/index.html")
    return HTMLResponse(content=file_path.read_text(), status_code=200)

# Include routers
app.include_router(matrix.router, prefix="/matrix/api", tags=["matrix"])
app.include_router(crm.router, prefix="/crm/api", tags=["crm"])

# Startup event
@app.on_event("startup")
async def startup_event():
    """Initialize services on startup"""
    logger.info(f"Starting {settings.app_name}...")
    
    # Initialize Matrix client
    try:
        from src.matrix_client import matrix_client
        
        # Register callback to store messages
        async def store_message_callback(message_data):
            logger.info(f"Received message: {message_data['sender']} - {message_data['body'][:100]}")
            # Store for monitoring
            recent_messages.append(message_data)
            # Keep only last 100 messages
            
            if len(recent_messages) > 100:
                recent_messages.pop(0)
            # Here you would call your CRM integration
            # await process_message_for_crm(message_data)
        
        matrix_client.add_message_callback(store_message_callback)
        
        await matrix_client.initialize()
        logger.info("Matrix client initialized")
    except ImportError as e:
        logger.error(f"Failed to import matrix_client: {e}")
    except Exception as e:
        logger.error(f"Failed to initialize Matrix client: {e}", exc_info=True)
    
    logger.info(f"{settings.app_name} started successfully on {settings.host}:{settings.port}")
    
# Debug endpoint to see all events
@app.get("/debug/events")
async def debug_events(limit: int = 50):
    """Debug endpoint to see all events received"""
    return {
        "total_events": len(all_events),
        "events": all_events[-limit:]
    }

# WebSocket for real-time event monitoring
@app.websocket("/ws/events")
async def websocket_events(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            # Send recent events
            await websocket.send_json({
                "type": "events",
                "data": all_events[-10:] if len(all_events) > 10 else all_events
            })
            await asyncio.sleep(1)
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
    finally:
        await websocket.close()

# Update the startup event to log ALL events
async def startup_event():
    """Initialize services on startup"""
    logger.info(f"Starting {settings.app_name}...")
    
    # Initialize Matrix client
    try:
        from src.matrix_client import matrix_client
        
        # Register callback to log ALL events for debugging
        async def debug_event_callback(event_data):
            logger.info(f"🔍 DEBUG Event received: {json.dumps(event_data, indent=2)}")
            all_events.append({
                "timestamp": datetime.now().isoformat(),
                "data": event_data
            })
            # Keep only last 1000 events
            if len(all_events) > 1000:
                all_events.pop(0)
        
        # Register callback to process messages for CRM
        async def process_message_for_crm(message_data):
            logger.info(f"📨 Processing message for CRM: {message_data.get('sender', 'unknown')}")
            logger.info(f"   Body: {message_data.get('body', 'No body')}")
            logger.info(f"   Room: {message_data.get('room_id', 'unknown')}")
            
            try:
                # Process through message service
                result = await message_service.process_matrix_message(message_data)
                if result.get("success"):
                    logger.info(f"✅ Message processed successfully. Ticket ID: {result.get('ticket_id')}")
                else:
                    logger.error(f"❌ Failed to process message: {result.get('error')}")
            except Exception as e:
                logger.error(f"Error processing message for CRM: {e}")
        
        # Register both callbacks
        matrix_client.add_message_callback(debug_event_callback)
        matrix_client.add_message_callback(process_message_for_crm)
        
        await matrix_client.initialize()
        logger.info("Matrix client initialized")
    except ImportError as e:
        logger.error(f"Failed to import matrix_client: {e}")
    except Exception as e:
        logger.error(f"Failed to initialize Matrix client: {e}", exc_info=True)
    
    logger.info(f"{settings.app_name} started successfully on {settings.host}:{settings.port}")

from fastapi.responses import FileResponse

@app.get("/debug")
async def debug_page():
    """Debug interface"""
    return FileResponse("src/views/monitoring/debug_monitor.html")

# Shutdown event
@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on shutdown"""
    logger.info("Shutting down...")
    try:
        from src.matrix_client import matrix_client
        await matrix_client.close()
        logger.info("Matrix client closed")
    except Exception as e:
        logger.error(f"Error shutting down Matrix client: {e}")
    logger.info("Shutdown complete")

if __name__ == "__main__":
    uvicorn.run(
        "src.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
        log_level=settings.log_level.lower()
    )
    