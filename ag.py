import os
import subprocess
import psutil
import platform
import time
import threading
import sys
import shutil
import requests  # pip install requests
import hashlib
from flask import Flask, jsonify, abort

app = Flask(__name__)

# ------------------------------------------------------------------------------
#                               Конфигурация
# ------------------------------------------------------------------------------

# Пример пользователей (расширяем по необходимости)
users = [
    {"name": "Alice",   "ip": "172.19.0.1"},
    {"name": "Bob",     "ip": "192.168.1.11"},
    {"name": "Charlie", "ip": "192.168.1.12"}
]

# URL обновления – должен указывать на raw-версию нового кода.
# Если вы хотите разные файлы для Linux и Windows, можно задать разные URL.
UPDATE_URL = ""
VERISONAPP = '1.0.5'

@app.route('/version', methods=['GET'])
def version_get():
    return jsonify({"Version": VERISONAPP}) 

# Интервал проверки обновлений (в секундах)
UPDATE_CHECK_INTERVAL = 60

# ------------------------------------------------------------------------------
#                        Вспомогательные функции
# ------------------------------------------------------------------------------

def is_windows():
    return platform.system().lower() == "windows"

def is_compiled():
    """
    Определяет, что агент запущен как бинарник.
    При компиляции через PyInstaller sys.argv[0] будет иметь расширение .exe.
    """
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

def file_hash(filename):
    with open(filename, "rb") as f:
        return compute_hash(f.read())

def normalize_code(data):
    """
    Нормализует содержимое кода:
      - Декодирует UTF-8,
      - Заменяет CRLF на LF,
      - Применяет strip() для удаления начальных и конечных пробелов и переводов строк,
      - Убирает пробелы в конце каждой строки,
      - Кодирует обратно в bytes.
    """
    try:
        text = data.decode('utf-8')
        text = text.replace('\r\n', '\n')
        text = text.strip()
        lines = [line.rstrip() for line in text.split('\n')]
        normalized = '\n'.join(lines)
        return normalized.encode('utf-8')
    except Exception as e:
        print("[ERROR] Ошибка нормализации:", e)
        return data

def code_has_changed(new_code):
    """
    Сравнивает нормализованное содержимое текущего файла (sys.argv[0]) с new_code.
    Возвращает кортеж (changed, current_hash, new_hash).
    """
    current_file = os.path.abspath(sys.argv[0])
    try:
        with open(current_file, "rb") as f:
            current_code = f.read()
        normalized_current = normalize_code(current_code)
        normalized_new = normalize_code(new_code)
        current_hash = compute_hash(normalized_current)
        new_hash = compute_hash(normalized_new)
        changed = current_hash != new_hash
        return changed, current_hash, new_hash
    except Exception as e:
        print("[ERROR] Ошибка при вычислении хэша:", e)
        return True, None, None

def check_for_updates():
    """
    Скачивает новый код с UPDATE_URL, нормализует его и сравнивает с текущим.
    Возвращает словарь с информацией об обновлении.
    """
    try:
        response = requests.get(UPDATE_URL, timeout=30)
        if response.status_code != 200:
            print("[ERROR] Ошибка скачивания обновления, статус:", response.status_code)
            return {"update_available": False, "error": f"HTTP {response.status_code}"}
        new_code = response.content
        changed, current_hash, new_hash = code_has_changed(new_code)
        if changed:
            return {
                "update_available": True,
                "current_hash": current_hash,
                "new_hash": new_hash,
                "update_url": UPDATE_URL
            }
        else:
            return {"update_available": False}
    except Exception as e:
        print("[ERROR] Ошибка при проверке обновлений:", str(e))
        return {"update_available": False, "error": str(e)}

def delayed_restart(delay, new_file):
    """
    Для скриптов: ждёт delay секунд, затем заменяет текущий процесс новым файлом.
    Используется os.execv, чтобы полностью заменить процесс.
    """
    time.sleep(delay)
    print("[INFO] Перезапуск: замена процесса новым файлом:", new_file)
    try:
        os.execv(sys.executable, [sys.executable, new_file] + sys.argv[1:])
    except Exception as e:
        print("[ERROR] Ошибка при вызове os.execv:", e)
        os._exit(1)

def delayed_restart_exe(delay, new_file):
    """
    Для бинарников (EXE): ждёт delay секунд, затем создаёт временный batch‑файл,
    который через небольшую задержку копирует новый EXE поверх текущего и запускает его.
    После запуска batch‑файл сам удаляет себя.
    """
    time.sleep(delay)
    current_exe = os.path.abspath(sys.argv[0])
    bat_file = os.path.join(os.path.dirname(current_exe), "update.bat")
    # Batch-сценарий:
    # timeout /t 3 ждет 3 секунды, затем копирует новый файл поверх текущего и запускает его.
    bat_contents = f"""@echo off
timeout /t 3 /nobreak > NUL
copy /Y "{new_file}" "{current_exe}"
start "" "{current_exe}"
del "%~f0"
"""
    try:
        with open(bat_file, "w") as f:
            f.write(bat_contents)
        print("[INFO] Batch-файл для обновления создан:", bat_file)
        subprocess.Popen(["cmd", "/c", bat_file], shell=True)
    except Exception as e:
        print("[ERROR] Ошибка при запуске batch-файла:", e)
    os._exit(0)

def perform_update_sync(update_url):
    """
    Синхронно скачивает новый код, нормализует и сравнивает его с текущим.
    Если обновление требуется, сохраняет новый код в файл с отметкой времени,
    удаляет старые файлы обновлений, делает резервную копию текущего файла
    и возвращает результат.
    """
    print("[INFO] Начало обновления по URL:", update_url)
    try:
        response = requests.get(update_url, timeout=30)
        if response.status_code != 200:
            msg = f"Ошибка скачивания обновления, статус: {response.status_code}"
            print("[ERROR]", msg)
            return {"success": False, "message": msg}
        
        new_code = response.content
        changed, current_hash, new_hash = code_has_changed(new_code)
        if not changed:
            msg = "Код не изменился. Обновление не требуется."
            print("[INFO]", msg)
            return {"success": False, "message": msg}
        
        # Определяем расширение файла:
        # Для бинарника (EXE) используем .exe, для скрипта на Windows — .pyw, иначе .py
        if is_compiled():
            new_file_ext = ".exe"
        else:
            new_file_ext = ".pyw" if is_windows() else ".py"
        timestamp = time.strftime("%Y%m%d%H%M%S")
        new_file_name = f"agent_new_{timestamp}{new_file_ext}"
        
        current_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
        # Удаляем предыдущие файлы обновлений с таким же расширением
        for fname in os.listdir(current_dir):
            if fname.startswith("agent_new_") and fname.endswith(new_file_ext):
                try:
                    os.remove(os.path.join(current_dir, fname))
                except Exception as e:
                    print("[WARNING] Не удалось удалить старый файл обновления:", e)
        
        print(f"[INFO] Сохраняем новый код в {new_file_name}")
        with open(new_file_name, "wb") as f:
            f.write(new_code)
        
        try:
            os.chmod(new_file_name, 0o755)
            print("[INFO] Права на выполнение установлены для", new_file_name)
        except Exception as e:
            print("[WARNING] Не удалось установить права на выполнение:", e)
        
        current_file = os.path.abspath(sys.argv[0])
        backup_file = current_file + ".bak"
        try:
            shutil.copy2(current_file, backup_file)
            print("[INFO] Резервная копия создана:", backup_file)
        except Exception as e:
            print("[WARNING] Не удалось создать резервную копию:", e)
        
        return {
            "success": True,
            "message": "Обновление прошло успешно. Перезапуск приложения через 2 секунды.",
            "current_hash": current_hash,
            "new_hash": new_hash,
            "new_file": new_file_name
        }
    except Exception as e:
        msg = f"Ошибка при обновлении: {str(e)}"
        print("[ERROR]", msg)
        return {"success": False, "message": msg}

# ------------------------------------------------------------------------------
#                            Endpoints (Flask)
# ------------------------------------------------------------------------------

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
        "directories": get_user_directories()
    })

@app.route('/connect/<username>/<metric_name>', methods=['GET'])
def connect_to_user_metric(username, metric_name):
    user = next((u for u in users if u['name'] == username), None)
    if not user:
        abort(404, description="User not found")
    metrics_data = get_metrics()
    if metric_name in metrics_data:
        return jsonify({metric_name: metrics_data[metric_name]})
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

def get_services():
    services = []
    if is_windows():
        for service in psutil.win_service_iter():
            try:
                services.append({
                    "name": service.name(),
                    "status": service.status(),
                    "display_name": service.display_name()
                })
            except Exception:
                pass
    else:
        result = subprocess.run(
            ["systemctl", "list-units", "--type=service", "--no-pager", "--no-legend"],
            stdout=subprocess.PIPE,
            text=True
        )
        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) > 1:
                services.append({"name": parts[0], "status": parts[2]})
    return services

@app.route('/services', methods=['GET'])
def list_services():
    return jsonify({"services": get_services()})

@app.route('/connect/<username>/services', methods=['GET'])
def connect_to_user_services(username):
    user = next((u for u in users if u['name'] == username), None)
    if not user:
        abort(404, description="User not found")
    return jsonify({"user": user, "services": get_services()})

@app.route('/update', methods=['POST', 'GET'])
def update_endpoint():
    """
    Принудительный вызов обновления:
      1. Проверяем обновления.
      2. Если обнаружено обновление, синхронно скачиваем новый файл и сохраняем его с отметкой времени.
      3. Если агент запущен как бинарник, запускаем delayed_restart_exe,
         иначе – delayed_restart.
    """
    update_info = check_for_updates()
    if update_info.get("update_available"):
        result = perform_update_sync(update_info["update_url"])
        if result.get("success"):
            if is_compiled():
                threading.Thread(target=delayed_restart_exe, args=(2, result["new_file"])).start()
            else:
                threading.Thread(target=delayed_restart, args=(2, result["new_file"])).start()
            return jsonify(result), 200
        else:
            return jsonify(result), 500
    else:
        return jsonify({"message": "Обновлений не обнаружено"}), 200

# ------------------------------------------------------------------------------
#                           Метрики и сервисы
# ------------------------------------------------------------------------------

def get_metrics():
    cpu_usage = psutil.cpu_percent(interval=1)
    memory_info = psutil.virtual_memory()
    disk_info = psutil.disk_usage('/')
    processes = []
    for proc in psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_info']):
        processes.append({
            "pid": proc.info['pid'],
            "name": proc.info['name'],
            "cpu_usage": f"{proc.info['cpu_percent']:.1f}%",
            "memory_usage": convert_bytes(proc.info['memory_info'].rss),
        })
    return {
        "cpu": {
            "usage": f"{cpu_usage}%",
            "description": "Текущая загрузка CPU"
        },
        "memory": {
            "total": convert_bytes(memory_info.total),
            "used": convert_bytes(memory_info.used),
            "free": convert_bytes(memory_info.free),
            "percent": f"{memory_info.percent}%",
            "description": "Использование оперативной памяти"
        },
        "disk": {
            "total": convert_bytes(disk_info.total),
            "used": convert_bytes(disk_info.used),
            "free": convert_bytes(disk_info.free),
            "percent": f"{disk_info.percent}%",
            "description": "Использование диска"
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
    system = platform.system().lower()
    path = "C:/Users" if is_windows() else "/home"
    try:
        return [d for d in os.listdir(path) if os.path.isdir(os.path.join(path, d))]
    except Exception as e:
        print("[WARNING] Ошибка при получении списка директорий:", e)
        return []

# ------------------------------------------------------------------------------
#                         Фоновая проверка обновлений
# ------------------------------------------------------------------------------

def background_update_checker():
    while True:
        update_info = check_for_updates()
        if update_info.get("update_available"):
            print("[INFO] Фоновая проверка: обнаружено обновление:", update_info)
        time.sleep(UPDATE_CHECK_INTERVAL)

# ------------------------------------------------------------------------------
#                               Запуск
# ------------------------------------------------------------------------------

if __name__ == '__main__':
    print("[INFO] Запущена новая версия агента. PID:", os.getpid())
    threading.Thread(target=background_update_checker, daemon=True).start()
    app.run(host='0.0.0.0', port=5000)
