FROM python:3.11-slim-bookworm

# Install Python packages
# libzim pip package includes prebuilt binaries for x86_64 linux
RUN pip install --no-cache-dir \
    nomadnet \
    rns \
    lxmf \
    libzim \
    markdownify \
    psutil

# Copy application files
WORKDIR /app
COPY zim_host.py micronify.py ./
COPY pages/ /root/.nomadnetwork/storage/pages/

# Copy entrypoint script
COPY docker-entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Expose NomadNet TCP interface port
EXPOSE 4242

ENTRYPOINT ["/entrypoint.sh"]
