# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Chicago Community Reticulum Mesh Network node - provides services over the Reticulum Network Stack for secure, resilient mesh communication. The node runs NomadNet pages and various bridge services.

## Key Dependencies

- **RNS (Reticulum Network Stack)**: Core networking library for mesh communication
- **LXMF**: Lightweight Extensible Message Format for messaging
- **NomadNet**: Hosts the `.mu` pages (Micron markup format)
- **libzim**: ZIM file reader for offline Wikipedia/StackOverflow browsing
- **markdownify**: HTML to Markdown conversion (extended to Micron format)
- **qreader/cv2**: QR code detection for the QR router bot

## Architecture

### ZIM Reader System
A client-server architecture for serving offline ZIM archives (Wikipedia, StackOverflow, etc.) over NomadNet:

- `zim_host.py` - Background worker that loads ZIM files and listens on localhost:6000 for commands (list_archives, request_path, search). Requires `ZIM_PATH` and `ZIM_AUTHKEY` environment variables.
- `micronify.py` - Converts HTML from ZIM files to NomadNet's Micron markup format, handling link rewriting and content cleanup.
- `pages/zr.mu` - NomadNet page that acts as the frontend, communicating with zim_host via multiprocessing.connection.

### RNS Bridge System (`projects/`)
TCP/UDP to Reticulum bridges for tunneling arbitrary protocols over the mesh:

- `rns_bridge_server.py` - Accepts RNS connections and forwards to a local TCP/UDP server. Creates persistent identity file for stable RNS address.
- `rns_bridge_client.py` - Local TCP/UDP server that forwards connections to a remote RNS destination.

### QR Router (`projects/qr_rns.py`)
LXMF bot that receives images, scans for QR-encoded LXMF messages, and delivers them to the mesh network. Also monitors configured webcam URLs for QR codes.

### NomadNet Pages (`pages/`)
Dynamic pages written in Python that output Micron markup:
- `index.mu` - Landing page with system stats and links
- `zr.mu` - ZIM file browser frontend

## Development Environment

This is a Debian VM - always activate the venv before running Python:

```bash
source /home/user/Projects/ChicagoNomadNet/venv/bin/activate
```

## Running Services

```bash
# Always activate venv first, then:

# ZIM host (requires ZIM files)
ZIM_PATH=/path/to/zim/files/ ZIM_AUTHKEY=secret python zim_host.py

# RNS Bridge Server (forwards RNS to local service)
python projects/rns_bridge_server.py <target_host> <target_port> <tcp|udp>

# RNS Bridge Client (creates local port forwarding to RNS destination)
python projects/rns_bridge_client.py <listen_port> <rns_destination_hex> <tcp|udp>

# QR Router bot
python projects/qr_rns.py
```

## Network Configuration

Chicago mesh node connection details:
- TCP: `rns.chicagonomad.net:4242`
- I2P: `fgtqx3pgwyd3bjcq4ojes47j7ynnw72luf2g3jeguhf5bbzkcuhq.b32.i2p`
- LoRa: 914.875 MHz, BW 125kHz, SF 8, CR 5

## Micron Markup Notes

When editing `.mu` files, note the Micron format specifics:
- Links: `` `[text`:destination] ``
- Colors: `` `Fxxx `` (foreground), `` `Bxxx `` (background)
- Headers: `>` prefix (repeating increases level)
- Bold: `` `!text`! ``, Italic: `` `*text`* ``
