import os
from src.config.supabase_config import get_supabase_client

def verify_pricing_data():
    client = get_supabase_client()
    
    print("\n" + "="*80)
    print("PRICING DATA VERIFICATION")
    print("="*80 + "\n")
    
    # 1. Check if columns exist
    print("1. Checking if cost columns exist...")
    try:
        result = client.table("chat_completion_requests").select("cost_usd, input_cost_usd, output_cost_usd, pricing_source").limit(1).execute()
        print("   ✅ All cost columns exist!")
    except Exception as e:
        print(f"   ❌ Error: {e}")
        return
    
    # 2. Get summary statistics
    print("\n2. Summary Statistics:")
    try:
        # Get total count
        total_result = client.table("chat_completion_requests").select("id", count="exact").execute()
        total_count = total_result.count
        
        # Get count with cost data
        with_cost = client.table("chat_completion_requests").select("id", count="exact").not_.is_("cost_usd", "null").execute()
        with_cost_count = with_cost.count
        
        # Get requests with cost for detailed stats
        cost_data = client.table("chat_completion_requests").select("cost_usd, input_cost_usd, output_cost_usd").not_.is_("cost_usd", "null").execute()
        
        if cost_data.data:
            costs = [float(r["cost_usd"]) for r in cost_data.data if r.get("cost_usd")]
            total_cost = sum(costs)
            avg_cost = total_cost / len(costs) if costs else 0
            min_cost = min(costs) if costs else 0
            max_cost = max(costs) if costs else 0
            
            print(f"   Total Requests: {total_count:,}")
            print(f"   Requests with Cost Data: {with_cost_count:,}")
            print(f"   Requests without Cost Data: {total_count - with_cost_count:,}")
            print(f"   Percentage with Cost: {(with_cost_count/total_count*100):.2f}%")
            print(f"   Total Cost: ${total_cost:,.6f}")
            print(f"   Average Cost per Request: ${avg_cost:.6f}")
            print(f"   Min Cost: ${min_cost:.6f}")
            print(f"   Max Cost: ${max_cost:.6f}")
    except Exception as e:
        print(f"   ❌ Error: {e}")
    
    # 3. Check recent requests with cost data
    print("\n3. Recent Requests with Cost Data (last 5):")
    try:
        recent = client.table("chat_completion_requests").select(
            "id, created_at, model_id, input_tokens, output_tokens, cost_usd, input_cost_usd, output_cost_usd, pricing_source, status"
        ).not_.is_("cost_usd", "null").order("created_at", desc=True).limit(5).execute()
        
        if recent.data:
            for r in recent.data:
                print(f"\n   ID: {r['id']}")
                print(f"   Created: {r['created_at']}")
                print(f"   Model ID: {r['model_id']}")
                print(f"   Tokens: {r['input_tokens']} in + {r['output_tokens']} out")
                print(f"   Cost: ${float(r['cost_usd']):.6f} (in: ${float(r['input_cost_usd']):.6f}, out: ${float(r['output_cost_usd']):.6f})")
                print(f"   Source: {r['pricing_source']}")
                print(f"   Status: {r['status']}")
        else:
            print("   No requests with cost data found")
    except Exception as e:
        print(f"   ❌ Error: {e}")
    
    # 4. Check by pricing source
    print("\n4. Cost Data by Pricing Source:")
    try:
        all_costs = client.table("chat_completion_requests").select(
            "pricing_source, cost_usd"
        ).not_.is_("cost_usd", "null").execute()
        
        if all_costs.data:
            # Group by pricing_source
            sources = {}
            for r in all_costs.data:
                source = r['pricing_source'] or 'null'
                if source not in sources:
                    sources[source] = {'count': 0, 'total_cost': 0}
                sources[source]['count'] += 1
                sources[source]['total_cost'] += float(r['cost_usd'])
            
            for source, data in sorted(sources.items(), key=lambda x: x[1]['count'], reverse=True):
                avg = data['total_cost'] / data['count']
                print(f"   {source}: {data['count']:,} requests, ${data['total_cost']:,.6f} total, ${avg:.6f} avg")
    except Exception as e:
        print(f"   ❌ Error: {e}")
    
    # 5. Check model_usage_analytics view
    print("\n5. Top 10 Models by Cost (from model_usage_analytics view):")
    try:
        analytics = client.table("model_usage_analytics").select(
            "model_name, provider_slug, successful_requests, total_cost_usd, avg_cost_per_request_usd"
        ).order("total_cost_usd", desc=True).limit(10).execute()
        
        if analytics.data:
            for idx, model in enumerate(analytics.data, 1):
                print(f"\n   {idx}. {model['model_name']} ({model['provider_slug']})")
                print(f"      Requests: {model['successful_requests']:,}")
                print(f"      Total Cost: ${float(model['total_cost_usd']):.6f}")
                print(f"      Avg Cost: ${float(model['avg_cost_per_request_usd']):.6f}")
        else:
            print("   No data in model_usage_analytics view")
    except Exception as e:
        print(f"   ❌ Error: {e}")
    
    print("\n" + "="*80)
    print("✅ Verification Complete!")
    print("="*80 + "\n")

if __name__ == "__main__":
    verify_pricing_data()
