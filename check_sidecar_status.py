import os
import sys
from dotenv import load_dotenv

print("🔍 Starting Sidecar Diagnostic...")

# 1. Check Imports
try:
    import supabase
    import openai
    print("✅ Libraries installed (supabase, openai)")
except ImportError as e:
    print(f"❌ Library missing: {e}")
    print("👉 Please run: pip install supabase openai")
    sys.exit(1)

# 2. Check Environment
load_dotenv()
url = os.getenv("SUPABASE_URL")
key = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImRneGtkZW5rYmFwaHphYmtjeWJxIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc1OTAxMTA2OCwiZXhwIjoyMDc0NTg3MDY4fQ.A2UCwyK2fVYTv6JUwPqv5sSoz9XvtErNcCn2B55hquk"

if not url:
    print("❌ SUPABASE_URL missing in .env")
    sys.exit(1)

print(f"✅ Environment loaded (URL: {url[:20]}...)")

# 3. Check Connection & Table
try:
    client = supabase.create_client(url, key)
    res = client.table("stories_memory").select("count", count="exact").execute()
    print(f"✅ Connection successful!")
    print(f"📊 Current rows in 'stories_memory': {res.count}")
    
    # 4. Check Magic Function
    try:
        rpc_test = client.rpc("hybrid_search", {
            "query_vec": [0.0]*1536, 
            "match_threshold": 0.0, 
            "match_count": 1
        }).execute()
        print("✅ RPC 'hybrid_search' exists and works")
    except Exception as e:
        print(f"❌ RPC Check Failed: {e}")
        print("👉 Did you run the SQL script in Supabase?")

except Exception as e:
    print(f"❌ Connection Failed: {e}")
    if "relation" in str(e) and "does not exist" in str(e):
        print("👉 Table 'stories_memory' does not exist. Run the SQL script!")
    
print("\nDone.")
