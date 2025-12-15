# ZimBot Indexer Next Steps

## Completed
- [x] Created `zimfast` pybind11 C++ wrapper exposing `getEntryByClusterOrder(idx)`
- [x] Removed slow zimscan fallback - zimfast is now required
- [x] Updated `zim_indexer.py` with simple single-threaded `index_archives_simple()`
- [x] Updated `zimbot.py` to call the simple indexer
- [x] Built zimfast successfully in venv

## Next Steps

### 1. Test zimfast with real ZIM file
```bash
source /home/user/Projects/ChicagoNomadNet/venv/bin/activate
python3 -c "
from zimfast import ZimReader
reader = ZimReader('/path/to/test.zim')
print(f'Total entries: {reader.entry_count()}')
# Test reading a few entries
for i in range(min(10, reader.entry_count())):
    result = reader.get_entry(i)
    if result:
        path, title, content, mime = result
        print(f'{i}: {path} ({len(content)} bytes)')
"
```

### 2. Install remaining dependencies in venv
```bash
source /home/user/Projects/ChicagoNomadNet/venv/bin/activate
pip install sentence-transformers chromadb beautifulsoup4 lxml RNS LXMF
```

### 3. Run full indexer test
```bash
source /home/user/Projects/ChicagoNomadNet/venv/bin/activate
export ZIM_PATH=/path/to/zim/files/
cd /home/user/Projects/ChicagoNomadNet
python -m projects.zimbot.zimbot
```

### 4. Monitor progress
- Look for "Indexing [archive]..." messages
- Check that entries are processed without minute-long pauses
- Verify ChromaDB collection grows (check `./chromadb_data/`)

### 5. If still too slow, consider parallelism later
The threaded code exists but was commented out due to segfaults. Options:
- Multiple processes instead of threads (each with own ZimReader)
- Ray or multiprocessing for better isolation
- Profile to identify remaining bottlenecks

## Files Modified
- `projects/zimbot/zimfast/zimfast.cpp` - New C++ wrapper
- `projects/zimbot/zimfast/setup.py` - Build config
- `projects/zimbot/zim_indexer.py` - Uses zimfast, simple indexer
- `projects/zimbot/zimbot.py` - Calls simple indexer
- `projects/zimbot/config.py` - Threading config (for future use)
