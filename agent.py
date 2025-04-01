"""
Агент с обновлением, мониторингом и управлением пользователями

Функционал (v 1.0):
-----------
1. Обновление агента:
   - is_windows()            : Определяет, запущен ли агент под Windows.
   - is_compiled()           : Проверяет, запущен ли агент как скомпилированный (.exe) или как скрипт (.py).
   - convert_bytes()         : Преобразует количество байт в удобочитаемый формат (B, KB, MB, ...).
   - compute_hash()          : Вычисляет SHA256 хэш для заданных данных.
   - normalize_code()        : Нормализует код (удаляет лишние переводы строк) для корректного сравнения.
   - code_has_changed()      : Сравнивает хэш текущего файла с новым кодом/бинарником.
   - check_for_updates()     : Проверяет наличие обновлений, загружая файл с UPDATE_URL.
   - perform_update_exe_direct(update_url)
                             : Обновление путем загрузки нового .exe и запуска его.
   - perform_update_compile_py(update_url)
                             : Обновление: загрузка .py, компиляция в .exe с помощью PyInstaller и запуск.
   - perform_update_script_py(update_url)
                             : Обновление, если агент запущен как .py (скачивание нового скрипта, резервное копирование и запуск).
   - do_update_if_available() : Единая точка входа для проверки и выполнения обновления.

2. Системный мониторинг:
   - get_metrics()           : Сбор метрик системы (загрузка CPU, память, диск, процессы, системная информация).
   - get_user_directories()  : Получение списка пользовательских директорий (C:/Users или /home).
   - get_services()          : Сбор списка запущенных сервисов (с учетом платформы Windows/Linux).
   - get_ip()                : Определяет основной IP адрес машины.
   - get_uptime()            : Вычисляет аптайм машины с момента загрузки в формате "Xd Xh Xm Xs".
   - get_disks()             : Получает список примонтированных дисков с информацией о точке монтирования, файловой системе и опциях.

3. Информация о пользователях:
   - update_user_login_info() : Обновляет статус пользователей (залогинен/разлогинен) и фиксирует время последнего входа.
   - get_user_login_info()    : Возвращает информацию о статусе пользователей с форматированным временем входа.
   - get_machine_info()       : Собирает данные о машине: hostname, IP, аптайм, список дисков и статус пользователей.

4. Фоновые процессы:
   - background_update_checker()   : Фоновая проверка обновлений с заданным интервалом.
   - background_user_status_updater(): Фоновый сбор информации о статусе пользователей.

Эндпойнты (Routes):
---------------------
- GET /version
      Возвращает текущую версию агента.

- GET/POST /update
      Инициирует проверку и выполнение обновления агента.

- GET /users
      Возвращает список заданных пользователей.

- GET /connect/<username>
      Возвращает информацию о пользователе, включая системные метрики, список директорий и данные о машине.

- GET /connect/<username>/<metric_name>
      Возвращает конкретную метрику системы (например, cpu, memory, disk) для указанного пользователя.

- GET /connect/<username>/directories
      Возвращает список пользовательских директорий.

- GET /metrics
      Возвращает метрики системы.

- GET /metrics/list
      Возвращает список всех доступных метрик.

- GET /connect/<username>/metrics/list
      Возвращает список доступных метрик для выбранного пользователя.

- GET /services
      Возвращает список запущенных сервисов.

- GET /connect/<username>/services
      Возвращает информацию о сервисах для указанного пользователя.

- GET /machine_info
      Возвращает подробную информацию о машине: hostname, IP, аптайм, примонтированные диски и статус пользователей.

========================================================
"""

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
    {"name": "Alice", "ip": "192.168.1.10"},
    {"name": "Bob", "ip": "192.168.1.11"},
    {"name": "Charlie", "ip": "192.168.1.12"}
]

# Укажите прямую ссылку (raw) на ваш .exe или .py в репозитории / файлохранилище
UPDATE_URL = "https://raw.githubusercontent.com/WrNekit/agent-updater/refs/heads/main/agent.py"

VERISONAPP = '1.0'
UPDATE_CHECK_INTERVAL = 60  # каждые 60 секунд проверка обновлений

# ------------------------------------------------------------------------------------
#                          Функции для обновления агента
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
    hash_func = hashlib.sha256()
    hash_func.update(data)
    return hash_func.hexdigest()

def normalize_code(data):
    """Убираем лишние \r\n из .py-кода, чтобы мелкие переводы строк не влияли на хэш."""
    try:
        text = data.decode('utf-8')
        text = text.replace('\r\n', '\n')
        text = text.strip()
        lines = [line.rstrip() for line in text.split('\n')]
        return '\n'.join(lines).encode('utf-8')
    except:
        return data

def code_has_changed(new_data, is_py=False):
    """Сравниваем хэш текущего файла (exe или py) с new_data."""
    try:
        current_file = os.path.abspath(sys.argv[0])
        
        # Проверка на существование файла
        if not os.path.exists(current_file):
            print(f"[ERROR] Текущий файл {current_file} не существует!")
            return True, None, None
        
        with open(current_file, "rb") as f:
            current_data = f.read()

        if is_py:
            old_norm = normalize_code(current_data)
            new_norm = normalize_code(new_data)
        else:
            old_norm = current_data
            new_norm = new_data

        # Вычисляем хэш для текущего и нового файла
        old_hash = compute_hash(old_norm)
        new_hash = compute_hash(new_norm)

        # Сравнение хэшей
        return old_hash != new_hash, old_hash, new_hash
    except FileNotFoundError as e:
        print(f"[ERROR] Файл не найден: {e}")
        return True, None, None
    except Exception as e:
        print(f"[ERROR] Ошибка при вычислении хэша: {e}")
        return True, None, None

def check_for_updates():
    """Скачивает UPDATE_URL, определяет, .exe или .py, сравнивает хэши."""
    try:
        lower_url = UPDATE_URL.lower()
        # Для Linux по умолчанию ожидаем Python-скрипт (.py)
        if is_windows():
            if lower_url.endswith(".exe"):
                file_type = "exe"
            elif lower_url.endswith(".py"):
                file_type = "py"
            else:
                file_type = "exe"  # по умолчанию для Windows
        else:
            file_type = "py"  # для Linux ожидается скрипт

        resp = requests.get(UPDATE_URL, timeout=30)
        if resp.status_code != 200:
            print("[ERROR] check_for_updates: HTTP", resp.status_code)
            return {"update_available": False, "error": f"HTTP {resp.status_code}"}

        new_data = resp.content
        if file_type == "py":
            changed, old_hash, new_hash = code_has_changed(new_data, is_py=True)
        else:
            changed, old_hash, new_hash = code_has_changed(new_data, is_py=False)

        # Добавим отладочную информацию для диагностики
        print(f"Old hash: {old_hash}")
        print(f"New hash: {new_hash}")
        print(f"Hashes changed: {changed}")

        if changed:
            return {
                "update_available": True,
                "file_type": file_type,
                "old_hash": old_hash,
                "new_hash": new_hash,
                "update_url": UPDATE_URL
            }
        else:
            # Если хэши не изменились, возвращаем False
            return {"update_available": False}
    except Exception as e:
        print("[ERROR] check_for_updates exception:", e)
        return {"update_available": False, "error": str(e)}

def perform_update_exe_direct(update_url):
    """Сценарий, если в репо лежит готовый .exe, а мы тоже .exe."""
    print("[INFO] Скачиваем готовый .exe:", update_url)
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

        if is_windows():
            proc = subprocess.Popen([new_exe], creationflags=subprocess.CREATE_NEW_PROCESS_GROUP, close_fds=True)
        else:
            proc = subprocess.Popen([new_exe], close_fds=True)
        print("[INFO] Новый процесс PID:", proc.pid)

        time.sleep(5)
        os._exit(0)
    except Exception as e:
        return {"success": False, "message": str(e)}

def perform_update_compile_py(update_url):
    print("[INFO] Скачиваем .py и компилируем через PyInstaller.")
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
            return {"success": False, "message": f"Не найден компилят: {dist_exe}"}

        new_exe = os.path.join(cdir, f"agent_new_{stamp}.exe")
        shutil.copyfile(dist_exe, new_exe)
        if is_windows():
            proc = subprocess.Popen([new_exe], creationflags=subprocess.CREATE_NEW_PROCESS_GROUP, close_fds=True)
        else:
            proc = subprocess.Popen([new_exe], close_fds=True)
        print("[INFO] Новый exe PID:", proc.pid)
        time.sleep(5)
        os._exit(0)
    except Exception as e:
        return {"success": False, "message": str(e)}

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

        if is_windows():
            proc = subprocess.Popen([sys.executable, new_py], creationflags=subprocess.CREATE_NEW_PROCESS_GROUP, close_fds=True)
        else:
            proc = subprocess.Popen([sys.executable, new_py], close_fds=True)
        print("[INFO] Новый процесс PID:", proc.pid)
        time.sleep(5)
        os._exit(0)
    except Exception as e:
        return {"success": False, "message": str(e)}

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
#                          Системные функции и метрики
# ------------------------------------------------------------------------------------
def get_metrics():
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

def get_user_directories():
    path = "C:/Users" if is_windows() else "/home"
    try:
        return [d for d in os.listdir(path) if os.path.isdir(os.path.join(path, d))]
    except:
        return []

def get_services():
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
#                Дополнительные функции: информация о машине
# ------------------------------------------------------------------------------------
def get_ip():
    """Определяет основной IP адрес машины."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip

def get_uptime():
    """Возвращает время работы машины с момента загрузки в формате 'Xd Xh Xm Xs'."""
    boot_time = psutil.boot_time()
    uptime_seconds = time.time() - boot_time
    days, rem = divmod(uptime_seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, seconds = divmod(rem, 60)
    return f"{int(days)}d {int(hours)}h {int(minutes)}m {int(seconds)}s"

def get_disks():
    """Возвращает список примонтированных дисков."""
    partitions = psutil.disk_partitions()
    disk_list = []
    for p in partitions:
        disk_list.append({
            "device": p.device,
            "mountpoint": p.mountpoint,
            "fstype": p.fstype,
            "opts": p.opts
        })
    return disk_list

# Глобальный словарь для хранения информации о статусе пользователей
user_login_info = {}

def update_user_login_info():
    """
    Обновляет информацию о залогиненных пользователях.
    Для каждого пользователя фиксируется, залогинен он или нет, и время последнего входа.
    """
    global user_login_info
    sessions = psutil.users()
    current_users = {}
    for session in sessions:
        username = session.name
        login_time = session.started  # метка времени входа
        # Если несколько сессий - берем самое позднее время входа
        if username in current_users:
            current_users[username] = max(current_users[username], login_time)
        else:
            current_users[username] = login_time

    # Обновляем информацию для всех пользователей, которые уже встречались или сейчас активны
    all_users = set(list(user_login_info.keys()) + list(current_users.keys()))
    for username in all_users:
        if username in current_users:
            user_login_info[username] = {
                "logged_in": True,
                "last_login": current_users[username]
            }
        else:
            if username in user_login_info:
                user_login_info[username]["logged_in"] = False
            else:
                user_login_info[username] = {"logged_in": False, "last_login": None}

def get_user_login_info():
    """
    Возвращает информацию о статусе пользователей с преобразованием времени входа в читаемый формат.
    """
    result = {}
    for username, info in user_login_info.items():
        if info["last_login"]:
            last_login_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(info["last_login"]))
        else:
            last_login_str = None
        result[username] = {
            "logged_in": info["logged_in"],
            "last_login": last_login_str
        }
    return result

def get_machine_info():
    """
    Собирает информацию о машине:
      - hostname
      - IP адрес
      - аптайм
      - список примонтированных дисков
      - статус пользователей (залогинен/разлогинен и время последнего входа)
    """
    return {
        "hostname": platform.node(),
        "ip": get_ip(),
        "uptime": get_uptime(),
        "disks": get_disks(),
        "user_status": get_user_login_info()
    }

# ------------------------------------------------------------------------------------
#                          Фоновый сбор информации о статусе пользователей
# ------------------------------------------------------------------------------------
def background_user_status_updater():
    while True:
        update_user_login_info()
        time.sleep(10)

# ------------------------------------------------------------------------------------
#                          Фоновая проверка обновлений
# ------------------------------------------------------------------------------------
def background_update_checker():
    while True:
        info = check_for_updates()
        if info.get("update_available"):
            print("[INFO] Фоновая проверка: есть обновление, пробуем обновиться.")
            res = do_update_if_available()
            if res.get("success"):
                print("[INFO] Успешно обновились, завершаем старый агент.")
                break
            else:
                print("[ERROR] Не удалось обновиться:", res.get("message"))
        time.sleep(UPDATE_CHECK_INTERVAL)

# ------------------------------------------------------------------------------------
#                                Flask эндпойнты
# ------------------------------------------------------------------------------------
@app.route('/version')
def version_get():
    return jsonify({"Version": VERISONAPP})

@app.route('/update', methods=['GET', 'POST'])
def update_endpoint():
    result = do_update_if_available()
    return jsonify(result), 200

@app.route('/users', methods=['GET'])
def list_users():
    return jsonify({"users": users})

@app.route('/connect/<username>', methods=['GET'])
def connect_to_user(username):
    user = next((u for u in users if u['name'] == username), None)
    if not user:
        abort(404, description="User not found")
    return jsonify({
        "user": user,
        "metrics": get_metrics(),
        "directories": get_user_directories(),
        "machine_info": get_machine_info()  # добавленная информация о машине
    })

@app.route('/connect/<username>/<metric_name>', methods=['GET'])
def connect_to_user_metric(username, metric_name):
    user = next((u for u in users if u['name'] == username), None)
    if not user:
        abort(404, description="User not found")
    mdata = get_metrics()
    if metric_name in mdata:
        return jsonify({metric_name: mdata[metric_name]})
    else:
        abort(404, description="Metric not found")

@app.route('/connect/<username>/directories', methods=['GET'])
def connect_to_user_directories(username):
    user = next((u for u in users if u['name'] == username), None)
    if not user:
        abort(404, description="User not found")
    return jsonify({"directories": get_user_directories()})

@app.route('/metrics', methods=['GET'])
def metrics():
    return jsonify(get_metrics())

@app.route('/metrics/list', methods=['GET'])
def metrics_list():
    """Возвращает список всех доступных метрик."""
    mdata = get_metrics()
    return jsonify({"available_metrics": list(mdata.keys())})

@app.route('/connect/<username>/metrics/list', methods=['GET'])
def connect_to_user_metrics_list(username):
    user = next((u for u in users if u['name'] == username), None)
    if not user:
        abort(404, description="User not found")
    mdata = get_metrics()
    return jsonify({"available_metrics": list(mdata.keys())})

@app.route('/services', methods=['GET'])
def list_services():
    return jsonify({"services": get_services()})

@app.route('/connect/<username>/services', methods=['GET'])
def connect_to_user_services(username):
    user = next((u for u in users if u['name'] == username), None)
    if not user:
        abort(404, description="User not found")
    return jsonify({"user": user, "services": get_services()})

@app.route('/machine_info', methods=['GET'])
def machine_info():
    """Возвращает подробную информацию о машине."""
    return jsonify(get_machine_info())

# ------------------------------------------------------------------------------------
#                                Запуск
# ------------------------------------------------------------------------------------
if __name__ == '__main__':
    print(f"[INFO] Запущена версия агента {VERISONAPP}, PID={os.getpid()}")
    threading.Thread(target=background_update_checker, daemon=True).start()
    threading.Thread(target=background_user_status_updater, daemon=True).start()
    app.run(host='0.0.0.0', port=5000, use_reloader=False)
