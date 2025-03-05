import os
import subprocess
import psutil
import platform
import time
import threading
import sys
import shutil
import requests  # Требуется установить: pip install requests
from flask import Flask, jsonify, abort

app = Flask(__name__)

# Пример пользователей
users = [
    {"name": "Alice", "ip": "172.19.0.1"},
    {"name": "Bob", "ip": "192.168.1.11"},
    {"name": "Charlie", "ip": "192.168.1.12"}
]

# Текущая версия приложения
CURRENT_VERSION = "1.0.0"
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

# Основные endpoints (список пользователей, метрики, директории, сервисы)
@app.route('/users', methods=['GET'])
def list_users():
    users_list = get_users()
    return jsonify({"users": users_list})

@app.route('/connect/<username>', methods=['GET'])
def connect_to_user(username):
    user = next((u for u in get_users() if u['name'] == username), None)
    if not user:
        abort(404, description="User not found")
    user_metrics = get_metrics()
    user_dirs = get_user_directories()
    return jsonify({
        "user": user,
        "metrics": user_metrics,
        "directories": user_dirs
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
    user_dirs = get_user_directories()
    return jsonify({"directories": user_dirs})

@app.route('/metrics/cpu', methods=['GET'])
def get_cpu_metrics():
    metrics_data = get_metrics()
    return jsonify({"cpu": metrics_data["cpu"]})

@app.route('/metrics/memory', methods=['GET'])
def get_memory_metrics():
    metrics_data = get_metrics()
    return jsonify({"memory": metrics_data["memory"]})

@app.route('/metrics/disk', methods=['GET'])
def get_disk_metrics():
    metrics_data = get_metrics()
    return jsonify({"disk": metrics_data["disk"]})

@app.route('/metrics/processes', methods=['GET'])
def get_processes_metrics():
    metrics_data = get_metrics()
    return jsonify({"processes": metrics_data["processes"]})

@app.route('/metrics/system_info', methods=['GET'])
def get_system_info_metrics():
    metrics_data = get_metrics()
    return jsonify({"system_info": metrics_data["system_info"]})

@app.route('/metrics/list', methods=['GET'])
def list_metrics():
    available_metrics = ["cpu", "memory", "disk", "processes", "system_info"]
    return jsonify({"available_metrics": available_metrics})

@app.route('/metrics', methods=['GET'])
def metrics():
    metrics_data = get_metrics()
    return jsonify(metrics_data)

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

# Функции для проверки и выполнения обновления
def check_for_updates():
    """
    Имитация проверки обновлений.
    В реальной реализации можно отправить HTTP-запрос к центральному серверу,
    который возвращает последнюю версию и URL для обновления.
    Здесь для примера считаем, что доступна версия 1.0.1.
    """
    latest_version = "1.0.1"
    if latest_version > CURRENT_VERSION:
        return {
            "update_available": True,
            "latest_version": latest_version,
            # В реальном случае этот URL должен указывать на файл с обновлением,
            # например, на raw-версию файла с GitHub или на сервер обновлений.
            "update_url": "https://raw.githubusercontent.com/WrNekit/agent-updater/refs/heads/main/test.py"
        }
    else:
        return {"update_available": False}

def perform_update(update_url):
    """
    Функция для выполнения обновления.
    Реализована полная схема:
      1. Скачивание новой версии через HTTP.
      2. Сохранение в временный файл.
      3. Создание резервной копии текущего файла.
      4. Замена текущего файла новым.
      5. Перезапуск приложения.
    """
    print("Начало обновления с URL:", update_url)
    try:
        # Скачивание обновления
        response = requests.get(update_url, timeout=30)
        if response.status_code != 200:
            print("Ошибка скачивания обновления, статус:", response.status_code)
            return
        new_code = response.content
        # Запись новой версии во временный файл
        new_file = "diplom_new.py"
        with open(new_file, "wb") as f:
            f.write(new_code)
        # Создание резервной копии текущего файла
        current_file = os.path.abspath(__file__)
        backup_file = current_file + ".bak"
        shutil.copy2(current_file, backup_file)
        # Замена текущего файла новым файлом
        shutil.move(new_file, current_file)
        print("Обновление завершено. Новая версия установлена. Перезапуск приложения...")
        # Перезапуск приложения
        os.execv(sys.executable, [sys.executable] + sys.argv)
    except Exception as e:
        print("Ошибка при обновлении:", str(e))

# Endpoint для проверки и запуска обновления
@app.route('/update', methods=['POST','GET'])
def update_endpoint():
    update_info = check_for_updates()
    if update_info["update_available"]:
        # Запускаем процесс обновления в отдельном потоке, чтобы не блокировать endpoint
        threading.Thread(target=perform_update, args=(update_info["update_url"],)).start()
        return jsonify({
            "message": "Процесс обновления запущен",
            "latest_version": update_info["latest_version"]
        }), 200
    else:
        return jsonify({"message": "Обновлений не обнаружено"}), 200

# Фоновая задача для периодической проверки обновлений
def background_update_checker():
    while True:
        update_info = check_for_updates()
        if update_info["update_available"]:
            print("Фоновая проверка: обнаружено обновление:", update_info)
            # Если нужно, можно автоматически запускать обновление:
            # threading.Thread(target=perform_update, args=(update_info["update_url"],)).start()
        time.sleep(UPDATE_CHECK_INTERVAL)

if __name__ == '__main__':
    # Запуск фоновой задачи в отдельном потоке (daemon-поток завершится при остановке приложения)
    threading.Thread(target=background_update_checker, daemon=True).start()
    app.run(host='0.0.0.0', port=5000)
