"""RSS feed configuration for security news aggregation."""

SECURITY_FEEDS = [
    {
        "name": "Krebs on Security",
        "url": "https://krebsonsecurity.com/feed/",
        "category": "security",
        "priority": 1,  # Higher priority = more likely to be included
    },
    {
        "name": "Ars Technica Security",
        "url": "https://feeds.arstechnica.com/arstechnica/security",
        "category": "security",
        "priority": 2,
    },
    {
        "name": "BleepingComputer",
        "url": "https://www.bleepingcomputer.com/feed/",
        "category": "security",
        "priority": 2,
    },
    {
        "name": "The Hacker News",
        "url": "https://feeds.feedburner.com/TheHackersNews",
        "category": "security",
        "priority": 2,
    },
    {
        "name": "CISA Alerts",
        "url": "https://www.cisa.gov/cybersecurity-advisories/all.xml",
        "category": "alerts",
        "priority": 1,
    },
    {
        "name": "Schneier on Security",
        "url": "https://www.schneier.com/feed/atom/",
        "category": "security",
        "priority": 1,
    },
    {
        "name": "SANS Internet Storm Center",
        "url": "https://isc.sans.edu/rssfeed.xml",
        "category": "alerts",
        "priority": 2,
    },
    {
        "name": "Threatpost",
        "url": "https://threatpost.com/feed/",
        "category": "security",
        "priority": 3,
    },
    {
        "name": "Dark Reading",
        "url": "https://www.darkreading.com/rss.xml",
        "category": "security",
        "priority": 3,
    },
]

# Keywords that boost a story's relevance (Steve Gibson topics)
PRIORITY_KEYWORDS = [
    # Encryption & Crypto
    "tls", "ssl", "certificate", "encryption", "cryptography", "quantum",
    "rsa", "aes", "elliptic curve", "key exchange",

    # Authentication
    "password", "passkey", "fido", "webauthn", "2fa", "mfa", "authentication",
    "biometric", "oauth", "sso",

    # Vulnerabilities
    "zero-day", "0-day", "cve", "vulnerability", "exploit", "buffer overflow",
    "rce", "remote code execution", "privilege escalation",

    # Malware & Attacks
    "ransomware", "malware", "botnet", "ddos", "phishing", "supply chain",
    "apt", "nation-state",

    # Privacy
    "privacy", "tracking", "surveillance", "fingerprinting", "vpn", "tor",

    # Platforms Steve covers
    "windows", "chrome", "firefox", "ios", "android", "linux",
    "microsoft", "google", "apple",

    # Classic Security Now topics
    "sqrl", "spinrite", "grc", "dns", "dnssec", "lets encrypt",
]
