import os
import sys
import time
import subprocess
import threading
import platform
import hashlib
import shutil
import requests
import psutil
import socket

from flask import Flask, jsonify, abort, request

app = Flask(__name__)

# ------------------------------------------------------------------------------------
#                                  Конфигурация
# ------------------------------------------------------------------------------------
users = [
    # Пример: при необходимости допишите свои IP
    {"name": "Alice", "ip": "192.168.1.100"},
    {"name": "Bob",   "ip": "192.168.1.101"},
    {"name": "Charlie", "ip": "192.168.1.102"}
]

# Прямая (raw) ссылка на .exe или .py в вашем репо/хостинге (для автообновления)
UPDATE_URL = "https://raw.githubusercontent.com/WrNekit/agent-updater/refs/heads/main/test_connect.py"

VERISONAPP = '1.0.1'
UPDATE_CHECK_INTERVAL = 60  # Проверяем обновления каждые 60 секунд

# ------------------------------------------------------------------------------------
#                      Определение локального IP, проверка
# ------------------------------------------------------------------------------------
def get_local_ip():
    """
    Определяет IP текущего хоста. Может быть неточным, если множество интерфейсов,
    но для локальной сети обычно нормально.
    """
    try:
        hostname = socket.gethostname()
        return socket.gethostbyname(hostname)
    except:
        return "127.0.0.1"

LOCAL_IP = get_local_ip()
print(f"[INFO] Локальный IP (определён): {LOCAL_IP}")

def is_local_ip(ip: str) -> bool:
    """
    Считаем IP «локальным», если совпадает с нашим LOCAL_IP
    или если это localhost / 127.0.0.1.
    """
    if not ip:
        return True
    ip_l = ip.strip().lower()
    if ip_l in ["127.0.0.1", "localhost", "0.0.0.0"]:
        return True
    return (ip_l == LOCAL_IP)

# ------------------------------------------------------------------------------------
#                        Вспомогательные функции
# ------------------------------------------------------------------------------------
def is_windows():
    return platform.system().lower() == "windows"

def is_compiled():
    """Проверяем, запущен ли агент как .exe (PyInstaller)."""
    return sys.argv[0].lower().endswith(".exe")

def convert_bytes(bytes_value):
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if bytes_value < 1024.0:
            return f"{bytes_value:.2f} {unit}"
        bytes_value /= 1024.0

def compute_hash(data):
    sha = hashlib.sha256()
    sha.update(data)
    return sha.hexdigest()

def normalize_code(data):
    """
    Убираем лишние \r\n из .py-кода, чтобы мелкие переводы строк не влияли на хэш.
    """
    try:
        text = data.decode('utf-8')
        text = text.replace('\r\n', '\n')
        text = text.strip()
        lines = [line.rstrip() for line in text.split('\n')]
        return '\n'.join(lines).encode('utf-8')
    except:
        return data

# ------------------------------------------------------------------------------------
#            Сравнение хэша текущего файла с загруженными данными
# ------------------------------------------------------------------------------------
def code_has_changed(new_data, is_py=False):
    """
    Сравниваем хэш текущего (exe или py) с new_data.
    Если is_py=True, нормализуем \n внутри.
    """
    try:
        current_file = os.path.abspath(sys.argv[0])
        with open(current_file, "rb") as f:
            current_data = f.read()

        if is_py:
            old_norm = normalize_code(current_data)
            new_norm = normalize_code(new_data)
        else:
            old_norm = current_data
            new_norm = new_data

        old_hash = compute_hash(old_norm)
        new_hash = compute_hash(new_norm)
        changed = (old_hash != new_hash)
        return changed, old_hash, new_hash
    except FileNotFoundError:
        print("[INFO] Текущий файл не найден — считаем, что код изменился.")
        return True, None, None
    except Exception as e:
        print("[ERROR] code_has_changed:", e)
        return True, None, None

# ------------------------------------------------------------------------------------
#                         Проверка наличия обновлений
# ------------------------------------------------------------------------------------
def check_for_updates():
    """
    1) Скачивает UPDATE_URL (exe или py).
    2) Сравнивает хэши.
    3) Возвращает {"update_available": bool, "file_type": "exe"/"py", ...}
    """
    try:
        lower_url = UPDATE_URL.lower()
        if lower_url.endswith(".exe"):
            file_type = "exe"
        elif lower_url.endswith(".py"):
            file_type = "py"
        else:
            file_type = "exe"  # по умолчанию (если расширение непонятно)

        resp = requests.get(UPDATE_URL, timeout=30)
        if resp.status_code != 200:
            print("[ERROR] check_for_updates:", resp.status_code)
            return {"update_available": False, "error": f"HTTP {resp.status_code}"}

        new_data = resp.content
        if file_type == "py":
            changed, old_hash, new_hash = code_has_changed(new_data, is_py=True)
        else:
            changed, old_hash, new_hash = code_has_changed(new_data, is_py=False)

        if changed:
            return {
                "update_available": True,
                "file_type": file_type,
                "old_hash": old_hash,
                "new_hash": new_hash,
                "update_url": UPDATE_URL
            }
        else:
            return {"update_available": False}
    except Exception as e:
        print("[ERROR] check_for_updates exception:", e)
        return {"update_available": False, "error": str(e)}

# ------------------------------------------------------------------------------------
#      Сценарий 1: обновление, если скачиваем готовый .exe (без перекомпиляции)
# ------------------------------------------------------------------------------------
def perform_update_exe_direct(update_url):
    print("[INFO] Обновление: скачиваем .exe:", update_url)
    try:
        resp = requests.get(update_url, timeout=30)
        if resp.status_code != 200:
            return {"success": False, "message": f"HTTP {resp.status_code}"}

        new_data = resp.content
        changed, old_hash, new_hash = code_has_changed(new_data, is_py=False)
        if not changed:
            return {"success": False, "message": "Файл .exe не изменился."}

        cdir = os.path.dirname(os.path.abspath(sys.argv[0]))
        stamp = time.strftime("%Y%m%d%H%M%S")
        new_exe = os.path.join(cdir, f"agent_new_{stamp}.exe")
        with open(new_exe, "wb") as f:
            f.write(new_data)
        print("[INFO] Новый exe сохранён:", new_exe)

        # Запуск
        new_proc = subprocess.Popen([new_exe], creationflags=subprocess.CREATE_NEW_PROCESS_GROUP, close_fds=True)
        print("[INFO] Новый процесс PID:", new_proc.pid)

        # Дадим пару секунд
        time.sleep(5)
        # Завершаем старый
        os._exit(0)
    except Exception as e:
        return {"success": False, "message": str(e)}

# ------------------------------------------------------------------------------------
#      Сценарий 2: скачиваем .py и компилируем в .exe (если агент .exe)
# ------------------------------------------------------------------------------------
def perform_update_compile_py(update_url):
    print("[INFO] Скачиваем .py -> компилируем PyInstaller.")
    try:
        resp = requests.get(update_url, timeout=30)
        if resp.status_code != 200:
            return {"success": False, "message": f"HTTP {resp.status_code}"}

        new_data = resp.content
        changed, old_hash, new_hash = code_has_changed(new_data, is_py=True)
        if not changed:
            return {"success": False, "message": "Исходный .py не изменился."}

        cdir = os.path.dirname(os.path.abspath(sys.argv[0]))
        stamp = time.strftime("%Y%m%d%H%M%S")
        new_py = os.path.join(cdir, f"agent_new_{stamp}.py")
        with open(new_py, "wb") as f:
            f.write(new_data)

        compile_cmd = [sys.executable, "-m", "PyInstaller", "--onefile", "--noconsole", new_py]
        res = subprocess.run(compile_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        if res.returncode != 0:
            print("[ERROR] PyInstaller error:\n", res.stdout)
            return {"success": False, "message": "PyInstaller failed."}

        base_name = os.path.splitext(os.path.basename(new_py))[0] + ".exe"
        dist_exe = os.path.join(cdir, "dist", base_name)
        if not os.path.isfile(dist_exe):
            return {"success": False, "message": f"Не найден скомпилированный exe: {dist_exe}"}

        new_exe = os.path.join(cdir, f"agent_new_{stamp}.exe")
        shutil.copyfile(dist_exe, new_exe)
        new_proc = subprocess.Popen([new_exe], creationflags=subprocess.CREATE_NEW_PROCESS_GROUP, close_fds=True)
        print("[INFO] Новый exe PID:", new_proc.pid)

        time.sleep(5)
        os._exit(0)
    except Exception as e:
        return {"success": False, "message": str(e)}

# ------------------------------------------------------------------------------------
#      Сценарий 3: если сам агент .py, а репо тоже .py (скриптовая замена)
# ------------------------------------------------------------------------------------
def perform_update_script_py(update_url):
    print("[INFO] Скачиваем новый .py (скриптовый режим).")
    try:
        resp = requests.get(update_url, timeout=30)
        if resp.status_code != 200:
            return {"success": False, "message": f"HTTP {resp.status_code}"}

        new_data = resp.content
        changed, old_hash, new_hash = code_has_changed(new_data, is_py=True)
        if not changed:
            return {"success": False, "message": "Новый скрипт не отличается."}

        cfile = os.path.abspath(sys.argv[0])
        cdir = os.path.dirname(cfile)
        stamp = time.strftime("%Y%m%d%H%M%S")
        new_py = os.path.join(cdir, f"agent_new_{stamp}.py")
        backup_py = os.path.join(cdir, f"agent_backup_{stamp}.py")

        with open(new_py, "wb") as f:
            f.write(new_data)
        shutil.move(cfile, backup_py)

        new_proc = subprocess.Popen([sys.executable, new_py], creationflags=subprocess.CREATE_NEW_PROCESS_GROUP, close_fds=True)
        print("[INFO] Новый процесс PID:", new_proc.pid)

        time.sleep(5)
        os._exit(0)
    except Exception as e:
        return {"success": False, "message": str(e)}

# ------------------------------------------------------------------------------------
#                Единая точка вызова обновления
# ------------------------------------------------------------------------------------
def do_update_if_available():
    info = check_for_updates()
    if not info.get("update_available"):
        return {"success": False, "message": "Обновлений не обнаружено"}

    ftype = info["file_type"]
    if ftype == "exe":
        if is_compiled():
            return perform_update_exe_direct(info["update_url"])
        else:
            print("[WARNING] Агент .py, но обновление .exe. Запуск .exe напрямую.")
            return perform_update_exe_direct(info["update_url"])
    else:
        # file_type == "py"
        if is_compiled():
            return perform_update_compile_py(info["update_url"])
        else:
            return perform_update_script_py(info["update_url"])

# ------------------------------------------------------------------------------------
#                        Получение (локальных) метрик
# ------------------------------------------------------------------------------------
def get_local_metrics():
    cpu_usage = psutil.cpu_percent(interval=1)
    memory_info = psutil.virtual_memory()
    disk_info = psutil.disk_usage('/')
    processes = []
    for proc in psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_info']):
        try:
            processes.append({
                "pid": proc.info['pid'],
                "name": proc.info['name'],
                "cpu_usage": f"{proc.info['cpu_percent']:.1f}%",
                "memory_usage": convert_bytes(proc.info['memory_info'].rss),
            })
        except:
            pass

    return {
        "cpu": {
            "usage": f"{cpu_usage}%",
            "description": "Текущая загрузка CPU"
        },
        "memory": {
            "total": convert_bytes(memory_info.total),
            "used": convert_bytes(memory_info.used),
            "free": convert_bytes(memory_info.available),
            "percent": f"{memory_info.percent}%",
        },
        "disk": {
            "total": convert_bytes(disk_info.total),
            "used": convert_bytes(disk_info.used),
            "free": convert_bytes(disk_info.free),
            "percent": f"{disk_info.percent}%",
        },
        "processes": processes,
        "last_update": time.strftime('%Y-%m-%d %H:%M:%S'),
        "system_info": {
            "os": platform.system(),
            "os_version": platform.version(),
            "architecture": platform.architecture()[0],
            "hostname": platform.node()
        }
    }

def get_local_directories():
    path = "C:/Users" if is_windows() else "/home"
    try:
        return [d for d in os.listdir(path) if os.path.isdir(os.path.join(path, d))]
    except:
        return []

def get_local_services():
    services = []
    if is_windows():
        for svc in psutil.win_service_iter():
            try:
                services.append({
                    "name": svc.name(),
                    "status": svc.status(),
                    "display_name": svc.display_name()
                })
            except:
                pass
    else:
        cmd = ["systemctl", "list-units", "--type=service", "--no-pager", "--no-legend"]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, text=True)
        for line in res.stdout.splitlines():
            parts = line.split()
            if len(parts) > 1:
                services.append({"name": parts[0], "status": parts[2]})
    return services

# ------------------------------------------------------------------------------------
#             Запросы к удалённому агенту (если IP не локальный)
# ------------------------------------------------------------------------------------
def fetch_remote_json(ip: str, path: str):
    """
    Общая функция для похода к удалённому агенту:
      GET http://<ip>:5000/<path>
    Возвращает dict (JSON) или {"error": "..."}
    """
    url = f"http://{ip}:5000/{path}"
    try:
        resp = requests.get(url, timeout=5)
        if resp.status_code == 200:
            return resp.json()
        else:
            return {"error": f"Remote agent HTTP {resp.status_code}"}
    except Exception as e:
        return {"error": str(e)}

# ------------------------------------------------------------------------------------
#                           Flask эндпойнты
# ------------------------------------------------------------------------------------
@app.route('/version')
def version_get():
    return jsonify({"Version": VERISONAPP, "LocalIP": LOCAL_IP})

@app.route('/update', methods=['GET', 'POST'])
def update_endpoint():
    result = do_update_if_available()
    return jsonify(result), 200

@app.route('/users', methods=['GET'])
def list_users():
    return jsonify({"users": users})

# -------------------- Метрики локальные --------------------
@app.route('/metrics', methods=['GET'])
def metrics():
    """Локальные метрики текущей машины."""
    return jsonify(get_local_metrics())

@app.route('/directories', methods=['GET'])
def directories():
    """Локальные директории (C:/Users или /home)."""
    return jsonify({"directories": get_local_directories()})

@app.route('/services', methods=['GET'])
def list_services():
    """Локальные сервисы."""
    return jsonify({"services": get_local_services()})

# -------------------- Метрики (удалённая логика) --------------------
@app.route('/connect/<username>/metrics', methods=['GET'])
def connect_to_user_metrics(username):
    """
    Если IP user локальный -> отдаём свои метрики,
    иначе -> http://user_ip:5000/metrics
    """
    user = next((u for u in users if u['name'] == username), None)
    if not user:
        abort(404, f"User '{username}' not found")
    ip = user["ip"]

    if is_local_ip(ip):
        return jsonify(get_local_metrics())
    else:
        data = fetch_remote_json(ip, "metrics")
        return jsonify(data)

@app.route('/connect/<username>/directories', methods=['GET'])
def connect_to_user_directories(username):
    """
    Если IP user локальный -> свои директории,
    иначе -> http://user_ip:5000/directories
    """
    user = next((u for u in users if u['name'] == username), None)
    if not user:
        abort(404, f"User '{username}' not found")
    ip = user["ip"]

    if is_local_ip(ip):
        dirs_ = get_local_directories()
        return jsonify({"directories": dirs_})
    else:
        data = fetch_remote_json(ip, "directories")
        return jsonify(data)

@app.route('/connect/<username>/services', methods=['GET'])
def connect_to_user_services(username):
    """
    Если IP user локальный -> свои сервисы,
    иначе -> http://user_ip:5000/services
    """
    user = next((u for u in users if u['name'] == username), None)
    if not user:
        abort(404, f"User '{username}' not found")
    ip = user["ip"]

    if is_local_ip(ip):
        return jsonify({"services": get_local_services()})
    else:
        data = fetch_remote_json(ip, "services")
        return jsonify(data)

# -------------------- Пример: connect/<username>  --------------------
@app.route('/connect/<username>', methods=['GET'])
def connect_to_user(username):
    """
    Раньше возвращал локальные метрики/директории. Теперь можем:
    - Если локальный IP -> вернуть "local" всё,
    - Иначе -> запросить remote /metrics и /directories
    """
    user = next((u for u in users if u['name'] == username), None)
    if not user:
        abort(404, description="User not found")
    ip = user["ip"]

    if is_local_ip(ip):
        return jsonify({
            "user": user,
            "metrics": get_local_metrics(),
            "directories": get_local_directories()
        })
    else:
        # Пример, как собрать в один ответ
        remote_metrics = fetch_remote_json(ip, "metrics")
        remote_dirs = fetch_remote_json(ip, "directories")
        return jsonify({
            "user": user,
            "metrics": remote_metrics,
            "directories": remote_dirs
        })

# ------------------------------------------------------------------------------------
#                       Фоновая проверка обновлений
# ------------------------------------------------------------------------------------
def background_update_checker():
    while True:
        info = check_for_updates()
        if info.get("update_available"):
            print("[INFO] Фоновая проверка: найдено обновление, пробуем обновиться.")
            res = do_update_if_available()
            if res.get("success"):
                print("[INFO] Успешно обновились, завершаем старый процесс.")
                break
            else:
                print("[ERROR] Фоновое обновление не удалось:", res.get("message"))
        time.sleep(UPDATE_CHECK_INTERVAL)

# ------------------------------------------------------------------------------------
#                                Запуск
# ------------------------------------------------------------------------------------
if __name__ == '__main__':
    print(f"[INFO] Запуск агента v{VERISONAPP}, PID={os.getpid()}, локальный IP={LOCAL_IP}")
    # Запускаем фоновой поток проверки обновлений
    threading.Thread(target=background_update_checker, daemon=True).start()
    # Запускаем Flask
    app.run(host='0.0.0.0', port=5000, use_reloader=False)
