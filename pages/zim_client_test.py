from multiprocessing.connection import Client
import time

# Client 1
conn = Client(('localhost', 6000), authkey="insecure".encode())

conn.send({"command":"list_archives"})

resp = conn.recv()
print(resp)

archives = resp["archives"]

conn.close()
conn = Client(('localhost', 6000), authkey="insecure".encode())
conn.send({ "command":"request_path", "archive":archives[0]} )
resp = conn.recv()
print(resp)

conn.close()