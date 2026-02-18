
import asyncio
import os
import sys

# Add src to path
sys.path.append(os.path.abspath('.'))

from src.services.provider_model_sync_service import trigger_full_sync

async def main():
    try:
        print("Triggering full sync (this may take a while)...")
        result = await trigger_full_sync()
        print(f"Sync Result: {result.get('success', False)}")
        
        # Check providers synced
        providers = result.get('providers', {})
        print(f"Providers Synced: {providers.get('synced_count', 0)}")
        
        # Check models synced
        models = result.get('models', {})
        print(f"Models Synced: {models.get('total_models_synced', 0)} from {models.get('providers_processed', 0)} providers")
        
        if models.get('errors'):
            print(f"Errors: {len(models.get('errors'))}")
            for err in models.get('errors')[:5]:
                print(f" - {err}")
                
    except Exception as e:
        print(f"Error triggering sync: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())
