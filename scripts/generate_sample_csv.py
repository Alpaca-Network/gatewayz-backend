import csv
import uuid
import random
from datetime import datetime, timedelta, timezone

# Configuration
NUM_ROWS = 100
PROVIDER_ID = 87  # Featherless (from previous context)
MODEL_ID = 101    # Qwen model (from previous context)
USER_ID = str(uuid.uuid4())

# CSV Header matching the table schema
HEADER = [
    "request_id",
    "user_id",
    "provider_id",
    "model_id",
    "timestamp",
    "latency_ms",
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "tokens_per_second",
    "status",
    "cost_usd",
    "metadata"
]

def generate_csv():
    filename = "model_request_time_series_sample.csv"
    
    with open(filename, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(HEADER)
        
        start_time = datetime.now(timezone.utc) - timedelta(days=1)
        
        for i in range(NUM_ROWS):
            # Generate realistic data
            request_id = str(uuid.uuid4())
            timestamp = (start_time + timedelta(minutes=i * 10)).isoformat()
            
            latency = random.randint(100, 2000)
            input_tokens = random.randint(50, 1000)
            output_tokens = random.randint(10, 500)
            total_tokens = input_tokens + output_tokens
            
            # Calculate TPS
            tps = round((total_tokens / latency) * 1000, 2) if latency > 0 else 0
            
            # 95% success rate
            status = "success" if random.random() > 0.05 else "error"
            
            # Simple cost calculation ($0.0002 per 1k tokens)
            cost = round(total_tokens * 0.0000002, 6)
            
            metadata = f'{{"region": "us-east-1", "source": "csv_import", "index": {i}}}'
            
            row = [
                request_id,
                USER_ID,
                PROVIDER_ID,
                MODEL_ID,
                timestamp,
                latency,
                input_tokens,
                output_tokens,
                total_tokens,
                tps,
                status,
                cost,
                metadata
            ]
            writer.writerow(row)
            
    print(f"✅ Generated {filename} with {NUM_ROWS} rows.")

if __name__ == "__main__":
    generate_csv()
