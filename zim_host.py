import os
from multiprocessing.connection import Listener
from libzim.reader import Archive
from libzim.search import Query, Searcher
from libzim.suggestion import SuggestionSearcher
import traceback
from urllib.parse import unquote
from micronify import html_to_micron
import sys

# Env vars for privacy
zimpath = os.environ["ZIM_PATH"] 
authkey = os.environ["ZIM_AUTHKEY"].encode()

if "ZIM_AUTHKEY" not in os.environ or "ZIM_PATH" not in os.environ:
        print("\n please set ZIM_PATH and ZIM_AUTHKEY in the environment")
        exit(-1)

# recommend mounting this as tmpfs for speed and to avoid wear from constant writing/deleting
# for example `sudo nano /etc/tmpfiles.d/volatile-subfolder.conf` then ` /run/nomadfiles 0777 v v 1h -`  then `sudo systemd-tmpfiles --create` 
file_storage_path = os.path.expanduser("~/.nomadnetwork/storage/files/tmp/") # where the tmp files are stoed on disk (don't forget trailing /)
file_url_path = "/file/tmp/" # where we link them to to download

DEFAULT_PAGE_SIZE_BYTES =  2**64 # Actually have pagination once we add styling for it
archive_lookup = dict() # map from name to index id (we use numbers to save space/bandwidth in href rewrites)
archives = []
archive_names = []

def load(zimfile_path):
    """
    Load zimfiles into archive lookup so we can search and use them
    """
    filenames = [x for x in os.listdir(zimfile_path)]
    i=0
    for file in sorted(filenames):
        if file.endswith(".zim"):
            name = file[:-4] # name without extension. Let's keep dates and lang for now, its useful
            archives.append(Archive(zimfile_path+file))
            archive_names.append(name)
            print(f"Loading {name}...")
            archive_lookup[name] = i
            i+=1

def request_path(archive_idx, path, last_path):
    if archive_idx >= len(archives) or archive_idx <0:
        return {"status": "error", "message":f"could not find archive {archive_idx}"}
    
    archive = archives[archive_idx]
    # archive = archive_lookup.get(archive_name, None)
    # if archive is None:
    #     return {"status": "error", "message":f"could not find archive {archive_name}"}
    
    entry = archive.main_entry
    if path is not None and len(path) > 0:
        path = unquote(path) # unquote the path for dealing with uincode and stuff
        if not archive.has_entry_by_path(path):
            # is it just a trailing slash issue?
            if archive.has_entry_by_path(path+"/"):
                path = path+"/"
            else: 
                return {"status": "error", "message":f"could not find path {path} in {archive_idx}"}
        entry = archive.get_entry_by_path(path)
        
    item = entry.get_item()
    if path is None:
        path = item.path # fill in path for main entry
        print("PATH="+path)

    content = decode_content_by_mimetype(item, path, archive_idx, last_path=last_path)
    return {"status":"ok", "title":item.title, "content":content, "size": item.size, "mimetype": item.mimetype, "archive": {"name": archive_names[archive_idx], "id": archive_idx}  }
    
def decode_content_by_mimetype(item, current_path, archive_idx, pre_truncate=-1, last_path=None):
    """
    try to decode the content based on the mimetype
    """
    mimetype = item.mimetype
    content = bytes(item.content)
    
    if pre_truncate > 0:
        content = content[:pre_truncate]
        
    if mimetype == "text/html":
        #TODO html to micron
        html = content.decode("UTF-8")
        return html_to_micron(html, current_path, extra_get_params={"a":archive_idx})
    # just straight text decode anything else thats text/
    if mimetype.startswith("text"):
        return content.decode("UTF-8", errors='ignore')
    
    # Can't turn it into a micron page, let the user download it
    # first move to a temp file
    filename = archive_names[archive_idx] + "_" + current_path.replace("/","__")
    # if we overwrite it... o well it was tempfs anyway
    # TODO: Could we eagerly cache links when parsing the HTML (to things like images and pdf files, and then if it's here, we don't need to write it?)
    # that could get us around the 60 second minimum polling
    with open(file_storage_path + filename, "wb+") as tmpfile:
        tmpfile.write(content)
        
    # calculate size string
    size_str = ""
    num = item.size  # in bytes
    for unit in ("", "KB", "MB", "GB", "TB"):
        if abs(num) < 1024.0:
            size_str= f"{num:3.1f} {unit}"
            break
        num /= 1024.0
        
    return f"`F66d`[Click here to download {item.title}`:{file_url_path}{filename}]`f  " +\
        "\n Note: You may need to wait for up to 60 seconds before you can download it. This is a limitation of nomadnets refreshing logic" + \
            "\nIf the download fails, try again in a few seconds. " + \
            f"\n This file is {size_str} bytes. Be mindful of your bandwidth!" +\
            f"\n\n`F44a`[<--Back`:/page/zr.mu`a={archive_idx}|p={last_path}]`f" if last_path is not None else "" 
    
    

def list_archives():
    return {"status": "ok", "archives": [{'name':name, "id":idx} for name,idx in archive_lookup.items() ]}

def search(archive_idx, needle, page_idx, page_size):
    if archive_idx >= len(archives) or archive_idx <0:
        return {"status": "error", "message":f"could not find archive {archive_idx}"}
    
    archive = archives[archive_idx]
    
    query = Query().set_query(needle)
    searcher = Searcher(archive)
    search = searcher.search(query)
    count = search.getEstimatedMatches()
    num_to_grab = min(count,page_size)
    result_pages = list(search.getResults(page_idx*page_size, num_to_grab))
    results = []
    
    for path in result_pages:
        item = archive.get_entry_by_path(path).get_item()
        # grab the page and pre-trnacte it to save cpu cycles on conversion
        content = decode_content_by_mimetype(item, path, archive_idx, pre_truncate=5000).strip()
        # truncate the result itself so we don't have HUGE results
        content = content[:1000]
        
        results.append({"title":item.title, "content":content, "size": item.size, "mimetype": item.mimetype, "path": path})
    
    return {"status": "ok", "archive": {"name": archive_names[archive_idx], "id": archive_idx} , "count": count, 'search_string': needle, "results":  results, "page":page_idx, "page_size": page_size}
   

def main_loop():
    listener = Listener(('localhost', 6000), authkey=authkey)
    running = True
    while running:
        try:
            conn = listener.accept()
            print('connection accepted from', listener.last_accepted)
            
            msg = conn.recv()
            print(msg)
            command = msg.get("command")
            resp = {"status":"error", "message": f"no handler for command={command}"}
            
            if command == "list_archives":
                resp = list_archives()
            elif command == "request_path":
                archive_id = int(msg.get("archive", -1))
                path = msg.get("path", None) # path requested
                last_path = msg.get("last_path",None)
                resp = request_path(archive_id, path, last_path)
                #print(resp.get("content","?"))
            elif command == "search":
                archive_id = int(msg.get("archive", -1))
                search_str = msg.get("search", "no search?")
                page = int(msg.get("page",0))
                resp = search(archive_id, search_str, page, 5)
                
            conn.send(resp)
            #print(resp)
            conn.close()
        except EOFError as e:
            print("Connection unexpectedly cancelled")
        except Exception as e:
            traceback.print_exc()

    listener.close()


load(zimpath)    
main_loop()
    
#result = request("wikipedia_en_all_mini_2024-04", "/A/Baseball")
#print(result)
# zim = Archive("test.zim")
# print(f"Main entry is at {zim.main_entry.get_item().path}")
# entry = zim.get_entry_by_path("home/fr")
# print(f"Entry {entry.title} at {entry.path} is {entry.get_item().size}b.")
# print(bytes(entry.get_item().content).decode("UTF-8"))

# searching using full-text index
# search_string = "Welcome"
# query = Query().set_query(search_string)
# searcher = Searcher(zim)
# search = searcher.search(query)
# search_count = search.getEstimatedMatches()
# print(f"there are {search_count} matches for {search_string}")
# print(list(search.getResults(0, search_count)))

# # accessing suggestions
# search_string = "kiwix"
# suggestion_searcher = SuggestionSearcher(zim)
# suggestion = suggestion_searcher.suggest(search_string)
# suggestion_count = suggestion.getEstimatedMatches()
# print(f"there are {suggestion_count} matches for {search_string}")
# print(list(suggestion.getResults(0, suggestion_count)))