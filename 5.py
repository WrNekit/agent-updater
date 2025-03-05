##НОВАЯ ВЕРСИЯ КОДА
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

# Пример пользователей
users = [
    {"name": "Alice", "ip": "172.19.0.1"},
    {"name": "Bob", "ip": "192.168.1.11"},
    {"name": "Charlie", "ip": "192.168.1.12"}
]

# URL обновления (должен указывать на raw-версию нового кода)
# В данном примере обновляется файл с расширением .pyw
UPDATE_URL = "https://raw.githubusercontent.com/WrNekit/agent-updater/refs/heads/main/5.py"
UPDATE_CHECK_INTERVAL = 60  # интервал проверки обновлений в секундах

def convert_bytes(bytes_value):
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if bytes_value < 1024.0:
            return f"{bytes_value:.2f} {unit}"
        bytes_value /= 1024.0

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
            "Description": "Использование CPU и памяти для процесса"
        })

    last_update = time.strftime('%Y-%m-%d %H:%M:%S')
    metrics = {
        "last_update": last_update,
        "system_info": {
            "Operating System": f"{platform.system()} {platform.version()}",
            "Architecture": platform.architecture()[0],
            "Hostname": platform.node()
        },
        "cpu": {
            "Usage": f"{cpu_usage}%",
            "Description": "Текущая загрузка процессора в процентах."
        },
        "memory": {
            "Total": convert_bytes(memory_info.total),
            "Used": convert_bytes(memory_info.used),
            "Free": convert_bytes(memory_info.free),
            "Usage": f"{memory_info.percent}%",
            "Description": "Общая память, использованная и свободная память, а также процент использования."
        },
        "disk": {
            "Total": convert_bytes(disk_info.total),
            "Used": convert_bytes(disk_info.used),
            "Free": convert_bytes(disk_info.free),
            "Usage": f"{disk_info.percent}%",
            "Description": "Информация о дисковом пространстве."
        },
        "processes": processes
    }
    return metrics

def get_users():
    return users

def get_user_directories():
    system = platform.system().lower()
    if system == 'windows':
        user_dirs = [d for d in os.listdir("C:/Users") if os.path.isdir(os.path.join("C:/Users", d))]
        return user_dirs
    elif system == 'linux':
        user_dirs = [d for d in os.listdir("/home") if os.path.isdir(os.path.join("/home", d))]
        return user_dirs
    return []

# Основные endpoints
@app.route('/users', methods=['GET'])
def list_users():
    return jsonify({"users": get_users()})

@app.route('/connect/<username>', methods=['GET'])
def connect_to_user(username):
    user = next((u for u in get_users() if u['name'] == username), None)
    if not user:
        abort(404, description="User not found")
    return jsonify({
        "user": user,
        "metrics": get_metrics(),
        "directories": get_user_directories()
    })

@app.route('/connect/<username>/<metric_name>', methods=['GET'])
def connect_to_user_metric(username, metric_name):
    user = next((u for u in get_users() if u['name'] == username), None)
    if not user:
        abort(404, description="User not found")
    metrics_data = get_metrics()
    if metric_name in metrics_data:
        return jsonify({metric_name: metrics_data[metric_name]})
    else:
        abort(404, description="Metric not found")

@app.route('/connect/<username>/directories', methods=['GET'])
def connect_to_user_directories(username):
    user = next((u for u in get_users() if u['name'] == username), None)
    if not user:
        abort(404, description="User not found")
    return jsonify({"directories": get_user_directories()})

@app.route('/metrics', methods=['GET'])
def metrics():
    return jsonify(get_metrics())

def get_services():
    services = []
    if platform.system().lower() == "windows":
        for service in psutil.win_service_iter():
            services.append({
                "name": service.name(),
                "status": service.status(),
                "display_name": service.display_name()
            })
    elif platform.system().lower() == "linux":
        result = subprocess.run(["systemctl", "list-units", "--type=service", "--no-pager", "--no-legend"],
                                stdout=subprocess.PIPE, text=True)
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
    user = next((u for u in get_users() if u['name'] == username), None)
    if not user:
        abort(404, description="User not found")
    return jsonify({"user": user, "services": get_services()})

# Функция вычисления хэша для данных
def compute_hash(data):
    hash_func = hashlib.sha256()
    hash_func.update(data)
    return hash_func.hexdigest()

def file_hash(filename):
    with open(filename, "rb") as f:
        return compute_hash(f.read())

def code_has_changed(new_code):
    current_file = os.path.abspath(sys.argv[0])
    try:
        current_code = open(current_file, "rb").read()
        current_hash = compute_hash(current_code)
        new_hash = compute_hash(new_code)
        return current_hash != new_hash, current_hash, new_hash
    except Exception as e:
        print("Ошибка при вычислении хэша:", e)
        return True, None, None

# Функция проверки обновлений с сравнением по хэшу
def check_for_updates():
    try:
        response = requests.get(UPDATE_URL, timeout=30)
        if response.status_code != 200:
            print("Ошибка скачивания обновления, статус:", response.status_code)
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
        print("Ошибка при проверке обновлений:", str(e))
        return {"update_available": False, "error": str(e)}

# Функция для отложенного перезапуска нового процесса
def delayed_restart(delay, new_file):
    time.sleep(delay)
    try:
        # Запускаем новый процесс с новым файлом
        subprocess.Popen([sys.executable, new_file])
    except Exception as e:
        print("Ошибка при запуске нового процесса:", e)
    # Завершаем текущий процесс
    sys.exit(0)

# Синхронная функция выполнения обновления, возвращающая результат
def perform_update_sync(update_url):
    try:
        response = requests.get(update_url, timeout=30)
        if response.status_code != 200:
            return {"success": False, "message": f"Ошибка скачивания обновления, статус: {response.status_code}"}
        new_code = response.content
        changed, current_hash, new_hash = code_has_changed(new_code)
        if not changed:
            return {"success": False, "message": "Код не изменился. Обновление не требуется."}
        # Запись новой версии во временный файл (для .pyw)
        new_file = "agent_new.pyw"
        with open(new_file, "wb") as f:
            f.write(new_code)
        # Создание резервной копии текущего файла
        current_file = os.path.abspath(sys.argv[0])
        backup_file = current_file + ".bak"
        try:
            shutil.copy2(current_file, backup_file)
        except Exception as e:
            print("Не удалось создать резервную копию:", e)
        return {
            "success": True,
            "message": "Обновление прошло успешно. Перезапуск приложения через 2 секунды.",
            "current_hash": current_hash,
            "new_hash": new_hash,
            "new_file": new_file
        }
    except Exception as e:
        return {"success": False, "message": f"Ошибка при обновлении: {str(e)}"}

# Endpoint для проверки и запуска обновления (синхронное выполнение)
@app.route('/update', methods=['POST', 'GET'])
def update_endpoint():
    update_info = check_for_updates()
    if update_info.get("update_available"):
        result = perform_update_sync(update_info["update_url"])
        if result.get("success"):
            # Запускаем отложенный перезапуск нового процесса в отдельном потоке,
            # чтобы успеть вернуть HTTP-ответ клиенту.
            threading.Thread(target=delayed_restart, args=(2, result["new_file"])).start()
            return jsonify(result), 200
        else:
            return jsonify(result), 500
    else:
        return jsonify({"message": "Обновлений не обнаружено"}), 200

# Фоновая задача для периодической проверки обновлений
def background_update_checker():
    while True:
        update_info = check_for_updates()
        if update_info.get("update_available"):
            print("Фоновая проверка: обнаружено обновление:", update_info)
            # Можно автоматически запускать обновление, например:
            # threading.Thread(target=perform_update_sync, args=(update_info["update_url"],)).start()
        time.sleep(UPDATE_CHECK_INTERVAL)

if __name__ == '__main__':
    threading.Thread(target=background_update_checker, daemon=True).start()
    app.run(host='0.0.0.0', port=5000)
