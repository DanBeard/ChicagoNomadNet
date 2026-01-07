# Security Now! NomadNet Page

A self-hosted security news digest for NomadNet, written in Steve Gibson's distinctive style using a locally fine-tuned LLM.

## Overview

This system generates daily security news digests styled after the "Security Now!" podcast. It:

1. **Scrapes training data** from Security Now! transcripts (grc.com)
2. **Fine-tunes Mistral 7B** using MLX-LM on Mac M1 Max
3. **Aggregates security news** from RSS feeds
4. **Generates daily digests** using the fine-tuned model
5. **Serves content** via NomadNet page

## Architecture

```
Mac M1 Max                    Linux Server                  NomadNet
(Fine-tuning)                 (Inference + Serving)         (Frontend)

scraper/ -----> training/ --> security_now.gguf
                              llama-server :8080
                              security_host.py :6001 <----> pages/sn.mu
                              news/ (cron: hourly)
                              generator/ (cron: daily)
```

## Quick Start

### 1. Install Dependencies (Linux)

```bash
cd /home/v/ChicagoNomadNet
source venv/bin/activate
pip install feedparser beautifulsoup4 requests
```

### 2. Fetch News

```bash
python -m projects.security_now.news.news_fetcher
```

### 3. Start Backend (without LLM for testing)

```bash
export SN_AUTHKEY=your_secret_key
python projects/security_now/security_host.py
```

### 4. Test the Page

Visit `/page/sn.mu` in NomadNet.

## Fine-Tuning (Mac)

### Prerequisites

```bash
pip install mlx-lm requests beautifulsoup4
git clone https://github.com/ggml-org/llama.cpp ~/llama.cpp
cd ~/llama.cpp && make -j
```

### Scrape Transcripts

```bash
python -m scraper.transcript_scraper --output ./transcripts --start 1 --end 1059
```

### Build Training Data

```bash
python -m scraper.dataset_builder ./transcripts ./training_data
```

### Train LoRA Adapter

```bash
./training/train_lora.sh
```

### Export to GGUF

```bash
./training/export_gguf.sh
scp security_now_mistral7b.gguf linux:/home/v/models/
```

## Production Setup

### Install Services

```bash
sudo ./services/install.sh
```

### Configure

Edit `/etc/systemd/system/security-host.service`:
- Set `SN_AUTHKEY` to match NomadNet environment

### Start Services

```bash
sudo systemctl enable security-host llama-server
sudo systemctl start security-host llama-server
```

## Configuration

Environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `SN_AUTHKEY` | insecure | Authentication key for backend |
| `SN_DB_PATH` | ./security_now.db | SQLite database path |
| `SN_HOST_PORT` | 6001 | Backend listen port |
| `SN_LLAMA_URL` | http://127.0.0.1:8080 | llama.cpp server URL |
| `SN_MODEL_VERSION` | v1 | Model version tag for digests |

## File Structure

```
projects/security_now/
├── config.py                 # Configuration
├── security_host.py          # Backend service (port 6001)
├── scraper/
│   ├── transcript_scraper.py # Download from grc.com
│   ├── transcript_processor.py # Parse speaker segments
│   └── dataset_builder.py    # Build training JSONL
├── training/
│   ├── config.yaml           # MLX-LM config
│   ├── train_lora.sh         # Training script (Mac)
│   ├── export_gguf.sh        # GGUF conversion
│   └── test_model.py         # Model testing
├── news/
│   ├── feed_config.py        # RSS feed URLs
│   ├── news_db.py            # SQLite storage
│   └── news_fetcher.py       # RSS aggregation
├── generator/
│   ├── prompt_templates.py   # Steve Gibson prompts
│   ├── llama_client.py       # llama.cpp HTTP client
│   └── digest_generator.py   # Daily generation
└── services/
    ├── security-host.service # systemd service
    ├── llama-server.service  # llama.cpp service
    ├── security-now.cron     # Cron jobs
    └── install.sh            # Installation script

pages/
└── sn.mu                     # NomadNet frontend
```

## News Sources

- Krebs on Security
- Ars Technica Security
- BleepingComputer
- The Hacker News
- CISA Alerts
- Schneier on Security
- SANS Internet Storm Center

## License

Part of the Chicago Nomad Network project.
