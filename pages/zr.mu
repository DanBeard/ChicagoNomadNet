#!/usr/bin/env python3
import os
import traceback
from multiprocessing.connection import Client

archive = os.environ.get("var_a", None)
path = os.environ.get("var_p", None)
last_path = os.environ.get("var_L", None)
page = int(os.environ.get("var_page", 0))
search = os.environ.get("field_search", None)
do_search = int(os.environ.get("var_do_search", "0")) > 0

# set this yourself in the env so we don't have a lingering RCE on the other side
authkey =  os.environ.get("ZIM_AUTHKEY", "insecure").encode()

#print(','.join(x for x in os.environ))
print("#!c=0") # don't cache, this is all dynamic
def send_cmd(conn, command, **kwargs):
    kwargs["command"] = command
    conn.send(kwargs)
    resp = conn.recv()
    if resp.get("status","nostatus") != "ok":
        print("ERROR!! ")
        print(resp.get("message", "no error message"))
        raise RuntimeError()
    return resp

def request_from_worker(archive, path):
    conn = Client(('localhost', 6000), authkey=authkey)
    try:
        # default, just list archives
        if archive is None:
            resp = send_cmd(conn, "list_archives")
            print("Below are Zim files in alphabetical order. Click on on to browse it. ")
            # TODO pagination for smaller bandwidth like Lora
            #print("They will be paginated (to accomidate slower connections) and images or other files can be downloaded through their /files/ links")
            print(">Archives")
            for archive in resp.get("archives",[]):
                print(f"`F55a`[{archive['name']}`:/page/zr.mu`a={archive['id']}]`f")
                #print("")
                
        elif do_search and search is not None:
            resp = send_cmd(conn, "search", archive=archive, search=search, page=page)
            page_size = int(resp.get("page_size", 1))
            count = int(resp.get("count",-1))
            num_pages = count/page_size
            results = resp.get("results",[])
            archive_name = resp.get("archive",{}).get("name","archive name")
            archive_id =  resp.get("archive",{}).get("id",0)
            # header
            print(f"`[Home`:/page/index.mu]                  `[{archive_name}`:/page/zr.mu`a={archive_id}]                  `B444`<16|search`{search}>`b `[Search`:/page/zr.mu`search|do_search=1|a={archive_id}]")
            print(f"-\n")
            next_page = f"`[Next Page`:/page/zr.mu`search|do_search=1|a={archive_id}|page={page+1}]" if page < num_pages else "          "  
            prev_page = f"`[Prev Page`:/page/zr.mu`search|do_search=1|a={archive_id}|page={page-1}]" if page > 0 else "      "
            print(f">{count} results for {search}. Showing page {page+1} of {num_pages}\n    {prev_page }   {next_page }  ")
            print("-=")
            i = 0
            for r in results:
                title, c, path = r.get("title","?"), r.get("content","???"), r.get("path","/")
                

                print(f"> Result {i}")
                i+=1
                print(f"`F44a`[{title}`:/page/zr.mu`a={archive_id}|p={path}]")
                if c is not None:
                    print(c)
                print("-=\n")
            
        # if we have an archive, then grab the path and display it
        else:
            resp = send_cmd(conn, "request_path", archive=archive, path=path, last_path=last_path)
            archive_name = resp.get("archive",{}).get("name","archive name")
            archive_id =  resp.get("archive",{}).get("id",0)
            search_str = search if search is not None else ""
            # header
            print(f"`[Home`:/page/index.mu]                  `[{archive_name}`:/page/zr.mu`a={archive_id}]                  "+
                  f"`B444`<16|search`{search_str}>`b `[Search`:/page/zr.mu`search|do_search=1|a={archive_id}]               " +
                  (f"`F44a`[<--Back`:/page/zr.mu`a={archive_id}|p={last_path}]`f" if last_path is not None else " ")
                  )
            print(f"-\n")
            print(resp.get("content","nocontent"))
    except RuntimeError as e:
        print("End")
    except Exception as e:
        traceback.print_exception(e)
    finally:
        conn.close()
        
        

try:
   request_from_worker(archive, path)
except Exception as e:
    traceback.print_exception(e)
# do it

#print(archive)
#print(path)   
      
