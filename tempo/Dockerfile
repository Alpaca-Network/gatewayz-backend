FROM grafana/tempo:2.6.1

# Run as root for Railway volume permissions
USER root

# Create data directories
RUN mkdir -p /var/tempo/traces /var/tempo/wal && \
    chmod -R 777 /var/tempo

COPY tempo.yml /etc/tempo/tempo.yml

# Expose Tempo ports
# 3200 - HTTP API (for Grafana queries)
# 4317 - OTLP gRPC (for trace ingestion)
# 4318 - OTLP HTTP (for trace ingestion)
EXPOSE 3200
EXPOSE 4317
EXPOSE 4318

CMD ["-config.file=/etc/tempo/tempo.yml"]
