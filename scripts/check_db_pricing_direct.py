#!/usr/bin/env python3
"""
Check database pricing directly via Supabase
"""

import os
from supabase import create_client

SUPABASE_URL = "https://ynleroehyrmaafkgjgmr.supabase.co"
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

if not SUPABASE_KEY:
    print("Error: SUPABASE_KEY environment variable not set")
    exit(1)

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

print("="*60)
print("Direct Database Pricing Query")
print("="*60)
print()

# Query for gpt-4o-mini pricing
try:
    # First, find the model
    models = supabase.table("models").select("*").eq("model_id", "openai/gpt-4o-mini").execute()

    if models.data:
        model = models.data[0]
        model_id = model["id"]
        print(f"Model found:")
        print(f"  ID: {model_id}")
        print(f"  Model ID: {model['model_id']}")
        print(f"  Name: {model.get('name', 'N/A')}")
        print()

        # Now check if there's pricing data
        pricing = supabase.table("model_pricing").select("*").eq("model_id", model_id).execute()

        if pricing.data:
            p = pricing.data[0]
            print(f"Pricing data found:")
            print(f"  Prompt price: ${p['price_per_input_token']:.10f}")
            print(f"  Completion price: ${p['price_per_output_token']:.10f}")
            print(f"  Updated at: {p.get('updated_at', 'N/A')}")
            print()

            # Calculate expected cost
            prompt_tokens = 8
            completion_tokens = 5
            expected_cost = (prompt_tokens * p['price_per_input_token']) + (completion_tokens * p['price_per_output_token'])

            print(f"Test calculation:")
            print(f"  Tokens: {prompt_tokens} prompt + {completion_tokens} completion")
            print(f"  Cost: ${expected_cost:.8f}")

            # Check if it's correct OpenRouter pricing
            correct_prompt = 0.00000015
            correct_completion = 0.0000006
            correct_cost = (prompt_tokens * correct_prompt) + (completion_tokens * correct_completion)

            print()
            print(f"Expected OpenRouter pricing:")
            print(f"  Prompt: ${correct_prompt:.10f}")
            print(f"  Completion: ${correct_completion:.10f}")
            print(f"  Expected cost: ${correct_cost:.8f}")

            if abs(p['price_per_input_token'] - correct_prompt) < 0.0000000001 and abs(p['price_per_output_token'] - correct_completion) < 0.0000000001:
                print()
                print("✅ Database pricing is CORRECT!")
            else:
                print()
                print("❌ Database pricing is INCORRECT!")

        else:
            print("❌ No pricing data found for this model in model_pricing table!")
            print()
            print("This explains why the system is using default pricing.")

    else:
        print("❌ Model not found in models table!")

except Exception as e:
    print(f"Error querying database: {e}")
