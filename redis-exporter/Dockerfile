FROM oliver006/redis_exporter:v1.66.0

# Run as root for Railway
USER root

# Expose metrics port
EXPOSE 9121

# ============================================================
# Redis Exporter for GatewayZ Backend
# ============================================================
# Connects to Redis and exposes Prometheus metrics at /metrics
#
# Required Environment Variables (set in Railway):
#   REDIS_ADDR - Redis connection address
#                Use Railway internal: redis.railway.internal:6379
#                Or reference variable: ${{Redis.RAILWAY_PRIVATE_DOMAIN}}:6379
#   REDIS_PASSWORD - Redis password
#                    Reference variable: ${{Redis.REDIS_PASSWORD}}
#
# The exporter will be available at:
#   Internal: redis-exporter.railway.internal:9121
#   Metrics endpoint: /metrics
# ============================================================

ENTRYPOINT ["/redis_exporter"]
