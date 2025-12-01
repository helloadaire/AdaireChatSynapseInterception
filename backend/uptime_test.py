#!/usr/bin/env python3
"""
Test script to verify the fixed Matrix client
"""

import asyncio
import os
import sys
from dotenv import load_dotenv

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

load_dotenv()

async def test_fixed_client():
    """Test the fixed Matrix client"""
    print("🧪 Testing fixed Matrix client...")
    
    try:
        # Import and test the fixed client
        from src.matrix_client import MatrixClient
        
        client = MatrixClient()
        
        # Mock callback for testing
        async def test_callback(message):
            print(f"📨 Callback received: {message['sender']} - {message['body'][:50]}")
        
        client.add_message_callback(test_callback)
        
        # Initialize
        await client.initialize()
        print("✅ Client initialized")
        
        # Get joined rooms
        rooms = await client.get_joined_rooms()
        print(f"✅ Joined {len(rooms)} rooms")
        
        if rooms:
            print("Sample rooms:")
            for room in rooms[:3]:
                print(f"  - {room}")
        
        # Close
        await client.close()
        print("✅ Test completed successfully")
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_fixed_client())