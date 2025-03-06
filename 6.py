import os
import subprocess
import psutil
import platform
import time
import threading
import sys
import shutil
import requests
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
UPDATE_URL = "https://raw.githubusercontent.com/WrNekit/agent-updater/refs/heads/main/4.py"

# Интервал проверки обновлений в секундах
UPDATE_CHECK_INTERVAL = 60

# ------------------------------------------------------------------------------
#                         Утилиты и вспомогательные функции
# ------------------------------------------------------------------------------

def is_windows():
    return platform.system().lower() == "windows"

def convert_bytes(bytes_value):
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if bytes_value < 1024.0:
            return f"{bytes_value:.2f} {unit}"
        bytes_value /= 1024.0

def compute_hash(data):
    """SHA-256 хэш для данных (bytes)."""
    hash_func = hashlib.sha256()
    hash_func.update(data)
    return hash_func.hexdigest()

def file_hash(filename):
    """SHA-256 хэш для содержимого файла."""
    with open(filename, "rb") as f:
        return compute_hash(f.read())

def code_has_changed(new_code):
    """
    Сравнивает хэш текущего исполняемого файла (sys.argv[0]) и new_code.
    Возвращает (изменился ли файл, current_hash, new_hash).
    """
    current_file = os.path.abspath(sys.argv[0])
    try:
        with open(current_file, "rb") as f:
            current_code = f.read()
        current_hash = compute_hash(current_code)
        new_hash = compute_hash(new_code)
        return current_hash != new_hash, current_hash, new_hash
    except Exception as e:
        print("[ERROR] Ошибка при вычислении хэша:", e)
        # Если не смогли вычислить хэш текущего файла, считаем что нужно обновляться
        return True, None, None

def check_for_updates():
    """
    Проверка обновлений: скачиваем файл по UPDATE_URL, сравниваем хэши.
    Возвращает словарь:
    {
      "update_available": bool,
      "current_hash": str or None,
      "new_hash": str or None,
      "update_url": str,
      "error": str (опционально)
    }
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
    Запускает новый файл (new_file) через delay секунд, затем останавливает текущий процесс.
    """
    time.sleep(delay)
    try:
        print("[INFO] Запуск нового процесса:", new_file)
        subprocess.Popen([sys.executable, new_file])
    except Exception as e:
        print("[ERROR] Ошибка при запуске нового процесса:", e)
    print("[INFO] Завершение текущего процесса.")
    sys.exit(0)

def perform_update_sync(update_url):
    """
    Синхронная функция выполнения обновления:
      1. Скачиваем новый код.
      2. Сравниваем хэш.
      3. Если нужно, записываем во временный файл.
      4. Делаем резервную копию.
      5. Возвращаем dict с результатом, где success = True/False.
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
        
        # Определяем расширение в зависимости от ОС
        new_file_ext = ".pyw" if is_windows() else ".py"
        new_file_name = "agent_new" + new_file_ext
        
        print(f"[INFO] Сохраняем новый код в {new_file_name}")
        with open(new_file_name, "wb") as f:
            f.write(new_code)

        # Создаём резервную копию
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

app = Flask(__name__)

@app.route('/users', methods=['GET'])
def list_users():
    return jsonify({"users": users})

@app.route('/connect/<username>', methods=['GET'])
def connect_to_user(username):
    """
    Эндпоинт, имитирующий «подключение» к пользователю в локальной сети.
    Возвращает метрики и список директорий (для демонстрации).
    """
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
    """
    Эндпоинт, позволяющий получить конкретную метрику (cpu, memory, disk и т.д.)
    по пользователю (условно).
    """
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
    """
    Эндпоинт, возвращающий список директорий (C:/Users/<...> или /home/<...>)
    для указанного пользователя.
    """
    user = next((u for u in users if u['name'] == username), None)
    if not user:
        abort(404, description="User not found")
    return jsonify({"directories": get_user_directories()})

@app.route('/metrics', methods=['GET'])
def metrics():
    """ Возвращает все метрики (CPU, память, диск, процессы). """
    return jsonify(get_metrics())

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
    2. Если есть, скачиваем и копируем.
    3. Запускаем отложенный перезапуск (через 2 секунды).
    """
    update_info = check_for_updates()
    if update_info.get("update_available"):
        result = perform_update_sync(update_info["update_url"])
        if result.get("success"):
            # Запускаем новый процесс через 2 секунды и завершаем текущий
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
    """
    Сбор различных метрик: CPU, память, диск, список процессов.
    """
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

def get_services():
    """
    Для Windows: выводит список служб через psutil.win_service_iter().
    Для Linux: через systemctl list-units --type=service.
    """
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
                # Бывает, что служба может вызвать ошибку при обращении
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

def get_user_directories():
    """
    Для Windows: возвращает список директорий в C:/Users/.
    Для Linux: список директорий в /home/.
    """
    system = platform.system().lower()
    if system == 'windows':
        path = "C:/Users"
    else:
        path = "/home"
    try:
        dirs = [
            d for d in os.listdir(path)
            if os.path.isdir(os.path.join(path, d))
        ]
        return dirs
    except Exception as e:
        print("[WARNING] Ошибка при получении списка директорий:", e)
        return []

# ------------------------------------------------------------------------------
#                         Фоновая проверка обновлений
# ------------------------------------------------------------------------------

def background_update_checker():
    """
    Периодически проверяет наличие обновлений.
    Если нужно — можно автоматически вызывать perform_update_sync() или что-то иное.
    """
    while True:
        update_info = check_for_updates()
        if update_info.get("update_available"):
            print("[INFO] Фоновая проверка: обнаружено обновление:", update_info)
            # Если хотим автообновление — раскомментировать:
            # result = perform_update_sync(update_info["update_url"])
            # if result.get("success"):
            #     threading.Thread(target=delayed_restart, args=(2, result["new_file"])).start()
        time.sleep(UPDATE_CHECK_INTERVAL)

# ------------------------------------------------------------------------------
#                               Запуск
# ------------------------------------------------------------------------------

if __name__ == '__main__':
    # Запуск фоновой проверки обновлений
    threading.Thread(target=background_update_checker, daemon=True).start()

    # Запуск Flask-приложения
    # Для production-режима рекомендуется использовать gunicorn/uwsgi и т.п.
    app.run(host='0.0.0.0', port=5000)
