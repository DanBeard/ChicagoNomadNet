#!/usr/bin/env python3
"""
RNS Client Bridge - Act as local TCP/UDP server, forward connections to RNS destination
"""

import RNS
import socket
import threading
import time
import argparse
import logging
import sys
from typing import Dict, Optional, Tuple

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('rns_client_bridge.log')
    ]
)
logger = logging.getLogger(__name__)

class ClientBridge:
    def __init__(self, listen_port: int, rns_destination: str, protocol: str, 
                 timeout: int = 900, listen_host: str = "127.0.0.1"):
        """
        Initialize the RNS Client Bridge
        
        Args:
            listen_port: Local port to listen on
            rns_destination: RNS destination hash to connect to
            protocol: 'tcp' or 'udp'
            timeout: Connection timeout in seconds (default: 15 minutes)
            listen_host: Local host to bind to
        """
        self.listen_host = listen_host
        self.listen_port = listen_port
        self.rns_destination_hash = bytes.fromhex(rns_destination)
        self.protocol = protocol.lower()
        self.timeout = timeout
        
        # Track active connections: local_socket -> (RNS.Link, last_activity)
        self.connections: Dict[socket.socket, Tuple[RNS.Link, float]] = {}
        self.connection_lock = threading.Lock()
        
        # Initialize RNS
        RNS.Reticulum()
        
        # Create ephemeral identity
        self.identity = RNS.Identity()
        logger.info(f"Created identity: {RNS.prettyhexrep(self.identity.hash)}")
        
        # Create server socket
        if self.protocol == 'tcp':
            self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        else:  # UDP
            self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        
        self.server_socket.bind((self.listen_host, self.listen_port))
        
        # Start cleanup thread
        self.cleanup_thread = threading.Thread(target=self._cleanup_connections, daemon=True)
        self.cleanup_thread.start()
        
        logger.info(f"Client bridge initialized")
        logger.info(f"Listening: {protocol.upper()} {listen_host}:{listen_port}")
        logger.info(f"RNS Target: {RNS.prettyhexrep(self.rns_destination_hash)}")
        logger.info(f"Timeout: {timeout} seconds")

    def _establish_rns_link(self, callback) -> Optional[RNS.Link]:
        """Establish a link to the RNS destination"""
        try:
            # Create destination identity
            destination_identity = RNS.Identity.recall(self.rns_destination_hash)
            if not destination_identity:
                logger.error("Could not recall destination identity, requesting path...")
                RNS.Transport.request_path(self.rns_destination_hash)
                time.sleep(2)  # Wait a bit for path
                destination_identity = RNS.Identity.recall(self.rns_destination_hash)
                
            if not destination_identity:
                logger.error("Failed to obtain destination identity")
                return None
            
            # Create destination
            destination = RNS.Destination(
                destination_identity,
                RNS.Destination.OUT,
                RNS.Destination.SINGLE,
                "bridge",
                "bridge_service"
            )
            
            # Establish link
            link = RNS.Link(destination)
            link.set_packet_callback(callback)
            
            # Wait for link to establish
            start_time = time.time()
            while link.status != RNS.Link.ACTIVE and time.time() - start_time < 10:
                time.sleep(0.1)
            
            if link.status == RNS.Link.ACTIVE:
                logger.info(f"Established RNS link to {RNS.prettyhexrep(self.rns_destination_hash)}")
                return link
            else:
                logger.error("Failed to establish RNS link")
                return None
                
        except Exception as e:
            logger.error(f"Error establishing RNS link: {e}")
            return None

    def _handle_tcp_client(self, client_socket: socket.socket, client_addr: Tuple[str, int]):
        """Handle TCP client connection"""
        logger.info(f"New TCP client connected: {client_addr}")
        
        try:
            with self.connection_lock:
                # Establish RNS link
                rns_link = self._establish_rns_link(lambda data, packet, sock=client_socket: self._rns_data_received(data, packet, sock))
                if not rns_link:
                    logger.error(f"Failed to establish RNS link for client {client_addr}")
                    client_socket.close()
                    return
                
                # Store connection
            
                self.connections[client_socket] = (rns_link, time.time())
                
                # Set packet callback for RNS link
                # rns_link.set_packet_callback(
                #     lambda data, packet, sock=client_socket: self._rns_data_received(data, packet, sock)
                # )
                
                # Handle client data
                client_socket.settimeout(10.0)
            
            while rns_link.status == RNS.Link.ACTIVE:
                try:
                    data = client_socket.recv(4096)
                    if not data:
                        break
                    
                    # Send to RNS
                    packet = RNS.Packet(rns_link, data)
                    packet.send()
                    
                    # Update last activity
                    with self.connection_lock:
                        if client_socket in self.connections:
                            self.connections[client_socket] = (rns_link, time.time())
                    
                    logger.debug(f"Forwarded {len(data)} bytes from TCP client to RNS")
                    
                except socket.timeout:
                    continue
                except Exception as e:
                    logger.error(f"Error handling TCP client data: {e}")
                    break
                    
        except Exception as e:
            logger.error(f"Error in TCP client handler: {e}")
        finally:
            self._cleanup_connection(client_socket)

    def _handle_udp_traffic(self):
        """Handle UDP traffic"""
        logger.info("Starting UDP traffic handler")
        
        # For UDP, we maintain one RNS link for all traffic
        rns_link = None
        client_addresses = {}  # Track client addresses
        
        try:
            self.server_socket.settimeout(1.0)
            
            while True:
                try:
                    data, client_addr = self.server_socket.recvfrom(4096)
                    logger.debug(f"Received UDP data from {client_addr}")
                    
                    # Establish RNS link if needed
                    if not rns_link or rns_link.status != RNS.Link.ACTIVE:
                        rns_link = self._establish_rns_link(lambda data: self._rns_udp_data_received(data, client_addresses))
                        if not rns_link:
                            logger.error("Failed to establish RNS link for UDP traffic")
                            continue
                        
                        # Set packet callback
                        # rns_link.set_packet_callback(
                        #     lambda data: self._rns_udp_data_received(data, client_addresses)
                        # )
                    
                    # Track client address
                    client_addresses[time.time()] = client_addr
                    
                    # Send to RNS
                    packet = RNS.Packet(rns_link, data)
                    packet.send()
                    
                    logger.debug(f"Forwarded {len(data)} bytes from UDP client to RNS")
                    
                except socket.timeout:
                    continue
                except Exception as e:
                    logger.error(f"Error handling UDP traffic: {e}")
                    time.sleep(1)
                    
        except Exception as e:
            logger.error(f"Error in UDP traffic handler: {e}")

    def _rns_data_received(self, data: bytes, packet, client_socket: socket.socket):
        """Handle data received from RNS for TCP"""
        try:
            with self.connection_lock:
                client_socket.send(data)
                 
                if client_socket in self.connections:
                    rns_link, _ = self.connections[client_socket]
                    # Update last activity
                    self.connections[client_socket] = (rns_link, time.time())
                    
                    logger.debug(f"Forwarded {len(data)} bytes from RNS to TCP client")
                else:
                    logger.debug(f"Forwarded {len(data)} bytes from RNS to TCP client [Warning: unknown socket {socket}]")
                    
        except Exception as e:
            logger.error(f"Error forwarding RNS data to TCP client: {e}")
            self._cleanup_connection(client_socket)

    def _rns_udp_data_received(self, data: bytes, client_addresses: dict):
        """Handle data received from RNS for UDP"""
        try:
            # For UDP, send to the most recent client address
            if client_addresses:
                latest_time = max(client_addresses.keys())
                client_addr = client_addresses[latest_time]
                
                self.server_socket.sendto(data, client_addr)
                logger.debug(f"Forwarded {len(data)} bytes from RNS to UDP client {client_addr}")
                
                # Clean up old addresses
                current_time = time.time()
                to_remove = [t for t in client_addresses.keys() if current_time - t > 300]
                for t in to_remove:
                    del client_addresses[t]
                    
        except Exception as e:
            logger.error(f"Error forwarding RNS data to UDP client: {e}")

    def _cleanup_connection(self, client_socket: socket.socket):
        """Clean up a specific connection"""
        with self.connection_lock:
            if client_socket in self.connections:
                rns_link, _ = self.connections[client_socket]
                try:
                    client_socket.close()
                    rns_link.teardown()
                except:
                    pass
                del self.connections[client_socket]
                logger.info("Cleaned up client connection")

    def _cleanup_connections(self):
        """Periodic cleanup of inactive connections"""
        while True:
            try:
                current_time = time.time()
                to_cleanup = []
                
                with self.connection_lock:
                    for client_socket, (rns_link, last_activity) in self.connections.items():
                        if current_time - last_activity > self.timeout:
                            to_cleanup.append(client_socket)
                
                for client_socket in to_cleanup:
                    logger.info("Connection timeout, cleaning up")
                    self._cleanup_connection(client_socket)
                
                time.sleep(30)  # Check every 30 seconds
                
            except Exception as e:
                logger.error(f"Error in cleanup thread: {e}")
                time.sleep(30)

    def start(self):
        """Start the client bridge"""
        logger.info("RNS Client Bridge started")
        
        try:
            if self.protocol == 'tcp':
                self.server_socket.listen(5)
                logger.info(f"Listening for TCP connections on {self.listen_host}:{self.listen_port}")
                
                while True:
                    try:
                        client_socket, client_addr = self.server_socket.accept()
                        client_thread = threading.Thread(
                            target=self._handle_tcp_client,
                            args=(client_socket, client_addr),
                            daemon=True
                        )
                        client_thread.start()
                        
                    except Exception as e:
                        logger.error(f"Error accepting TCP connection: {e}")
                        
            else:  # UDP
                logger.info(f"Listening for UDP traffic on {self.listen_host}:{self.listen_port}")
                self._handle_udp_traffic()
                
        except KeyboardInterrupt:
            logger.info("Shutting down client bridge...")
            self.shutdown()
        except Exception as e:
            logger.error(f"Error in client bridge: {e}")
            self.shutdown()

    def shutdown(self):
        """Shutdown the client bridge"""
        logger.info("Shutting down all connections...")
        
        try:
            self.server_socket.close()
        except:
            pass
        
        with self.connection_lock:
            for client_socket, (rns_link, _) in self.connections.items():
                try:
                    client_socket.close()
                    rns_link.teardown()
                except:
                    pass
            self.connections.clear()
        
        logger.info("Client bridge shutdown complete")


def main():
    parser = argparse.ArgumentParser(description='RNS Client Bridge')
    parser.add_argument('listen_port', type=int, help='Local port to listen on')
    parser.add_argument('rns_destination', help='RNS destination hash (hex)')
    parser.add_argument('protocol', choices=['tcp', 'udp'], help='Protocol (tcp or udp)')
    parser.add_argument('--host', default='127.0.0.1',
                       help='Local host to bind to (default: 127.0.0.1)')
    parser.add_argument('--timeout', type=int, default=900,
                       help='Connection timeout in seconds (default: 900)')
    parser.add_argument('--verbose', '-v', action='store_true',
                       help='Enable verbose logging')
    
    args = parser.parse_args()
    
    if True or args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    # Validate RNS destination hash
    try:
        bytes.fromhex(args.rns_destination)
    except ValueError:
        logger.error("Invalid RNS destination hash format")
        sys.exit(1)
    
    try:
        bridge = ClientBridge(
            listen_port=args.listen_port,
            rns_destination=args.rns_destination,
            protocol=args.protocol,
            timeout=args.timeout,
            listen_host=args.host
        )
        bridge.start()
        
    except Exception as e:
        logger.error(f"Failed to start client bridge: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()