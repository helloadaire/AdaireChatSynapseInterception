# test_e2ee_setup.py
import os
import sys
from dotenv import load_dotenv

# Load .env directly
load_dotenv()

print("🔍 Checking E2EE setup...")
print("=" * 50)

# Check environment variables
env_vars = ['MATRIX_RECOVERY_KEY', 'MESSAGE_RECOVERY_KEY', 'RECOVERY_KEY']
found_key = None
found_name = None

for var in env_vars:
    if var in os.environ:
        found_key = os.environ[var]
        found_name = var
        print(f"✅ Found recovery key in {var}: {found_key[:20]}...")
        break

if not found_key:
    print("❌ No recovery key found in .env file")
    print("\nPlease add one of these to your .env file:")
    print("MATRIX_RECOVERY_KEY=your_recovery_key_here")
    print("or")
    print("MESSAGE_RECOVERY_KEY=your_recovery_key_here")

# Check Python packages
print("\n🔧 Checking Python packages...")
try:
    import matrix_nio
    print(f"✅ matrix-nio version: {matrix_nio.__version__}")
except ImportError:
    print("❌ matrix-nio not installed")

try:
    from nio.crypto import Olm
    print("✅ E2EE dependencies are installed")
except ImportError as e:
    print(f"❌ E2EE dependencies missing: {e}")
    print("\n💡 Run: pip install 'matrix-nio[e2ee]'")

print("\n" + "=" * 50)
print("📋 Summary:")
if found_key:
    print(f"✅ Recovery key: {found_name} = {found_key[:20]}...")
else:
    print("❌ No recovery key found")
    
print("\n🚀 Next steps:")
print("1. Run: pip install 'matrix-nio[e2ee]'")
print("2. Update config/settings.py to include matrix_recovery_key field")
print("3. Restart your server")