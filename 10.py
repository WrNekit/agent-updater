import os
import subprocess
import psutil
import platform
import time
import threading
import sys
import shutil
import requests  # Требуется установить: pip install requests
import hashlib
from flask import Flask, jsonify, abort

app = Flask(__name__)

# ------------------------------------------------------------------------------
#                               Конфигурация
# ------------------------------------------------------------------------------

# Пример пользователей (можно расширять)
users = [
    {"name": "Alice",   "ip": "172.19.0.1"},
    {"name": "Bob",     "ip": "192.168.1.11"},
    {"name": "Charlie", "ip": "192.168.1.12"}
]

# URL обновления (должен указывать на raw-версию нового кода).
# Если вы хотите, чтобы на Linux обновлялся .py, а на Windows — .pyw,
# можно использовать разные ветки или разные файлы на сервере.
UPDATE_URL = "https://raw.githubusercontent.com/WrNekit/agent-updater/refs/heads/main/6.py"

# Интервал проверки обновлений в секундах
UPDATE_CHECK_INTERVAL = 60

# ------------------------------------------------------------------------------
#                         Вспомогательные функции
# ------------------------------------------------------------------------------

def is_windows():
    return platform.system().lower() == "windows"

def convert_bytes(bytes_value):
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if bytes_value < 1024.0:
            return f"{bytes_value:.2f} {unit}"
        bytes_value /= 1024.0

def compute_hash(data):
    """Вычисляет SHA-256 хэш для данных (bytes)."""
    hash_func = hashlib.sha256()
    hash_func.update(data)
    return hash_func.hexdigest()

def file_hash(filename):
    """Вычисляет SHA-256 хэш содержимого файла."""
    with open(filename, "rb") as f:
        return compute_hash(f.read())

def normalize_code(data):
    """
    Нормализует содержимое кода:
      - Декодирует UTF-8,
      - Приводит переводы строк к "\n",
      - Убирает пробелы в конце каждой строки,
      - Кодирует обратно в bytes.
    """
    try:
        text = data.decode('utf-8')
        text = text.replace('\r\n', '\n')
        text = "\n".join(line.rstrip() for line in text.split("\n"))
        return text.encode('utf-8')
    except Exception as e:
        print("[ERROR] Ошибка нормализации:", e)
        return data

def code_has_changed(new_code):
    """
    Сравнивает нормализованное содержимое текущего исполняемого файла (sys.argv[0])
    и new_code. Возвращает (изменился ли, текущий хэш, новый хэш).
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
    Ждёт delay секунд, затем заменяет текущий процесс новым файлом.
    Функция вызывает os.execv, что полностью заменяет текущий процесс.
    """
    time.sleep(delay)
    print("[INFO] Перезапуск: замена процесса новым файлом:", new_file)
    try:
        os.execv(sys.executable, [sys.executable, new_file])
    except Exception as e:
        print("[ERROR] Ошибка при вызове os.execv:", e)
        os._exit(1)

def perform_update_sync(update_url):
    """
    Синхронно скачивает новый код, нормализует и сравнивает его с текущим,
    записывает новый код во временный файл с отметкой времени,
    создаёт резервную копию и возвращает результат.
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
        
        # Определяем расширение файла в зависимости от ОС
        new_file_ext = ".pyw" if is_windows() else ".py"
        # Формируем имя файла с отметкой времени (ГГГГММДДЧЧММСС)
        timestamp = time.strftime("%Y%m%d%H%M%S")
        new_file_name = f"agent_new_{timestamp}{new_file_ext}"
        
        # Удаляем предыдущие файлы обновлений
        current_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
        for fname in os.listdir(current_dir):
            if fname.startswith("agent_new_") and fname.endswith(new_file_ext):
                try:
                    os.remove(os.path.join(current_dir, fname))
                except Exception as e:
                    print("[WARNING] Не удалось удалить старый файл обновления:", e)
        
        print(f"[INFO] Сохраняем новый код в {new_file_name}")
        with open(new_file_name, "wb") as f:
            f.write(new_code)

        # Создаём резервную копию текущего файла
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
    Принудительный вызов обновления.
    1. Проверяем обновления.
    2. Если есть, скачиваем и копируем с созданием нового файла с отметкой времени.
    3. Запускаем отложенный перезапуск (через 2 секунды), который заменяет текущий процесс.
    """
    update_info = check_for_updates()
    if update_info.get("update_available"):
        result = perform_update_sync(update_info["update_url"])
        if result.get("success"):
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
    """
    Периодически проверяет наличие обновлений.
    Если обнаружено обновление, выводит информацию в лог.
    """
    while True:
        update_info = check_for_updates()
        if update_info.get("update_available"):
            print("[INFO] Фоновая проверка: обнаружено обновление:", update_info)
            # Если требуется автообновление, можно вызвать perform_update_sync здесь.
        time.sleep(UPDATE_CHECK_INTERVAL)

# ------------------------------------------------------------------------------
#                               Запуск
# ------------------------------------------------------------------------------

if __name__ == '__main__':
    threading.Thread(target=background_update_checker, daemon=True).start()
    app.run(host='0.0.0.0', port=5000)
