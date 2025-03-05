import os
import subprocess
import psutil
import platform
import time
from flask import Flask, jsonify, abort
import requests
import sys

app = Flask(__name__)

users = [
    {"name": "Alice", "ip": "172.19.0.1"},
    {"name": "Bob", "ip": "192.168.1.11"},
    {"name": "Charlie", "ip": "192.168.1.12"}
]

REPO_URL = "https://raw.githubusercontent.com/WrNekit/agent-updater/refs/heads/main/diplom.py"  # URL репозитория с кодом агента
VERSION_URL = "https://raw.githubusercontent.com/WrNekit/agent-updater/refs/heads/main/version.txt"  # URL версии агента в репозитории
LOCAL_SCRIPT_PATH = "agent.pyw"  # .pyw для запуска без консоли

CURRENT_VERSION = "1.0.1"  # Текущая версия агента, которую вы указали в коде

# Функция для скачивания последней версии скрипта
def update_agent():
    response = requests.get(REPO_URL)
    if response.status_code == 200:
        response.encoding = 'utf-8'
        try:
            with open(LOCAL_SCRIPT_PATH, 'w', encoding='utf-8') as f:
                f.write(response.text)
            return True
        except Exception as e:
            return False
    return False

# Функция для перезапуска агента
def restart_agent():
    # Завершаем текущий процесс и перезапускаем его с pythonw
    if platform.system().lower() == 'windows':
        # Если это Windows, используем pythonw.exe для запуска без консоли
        executable = sys.executable.replace('python.exe', 'pythonw.exe')
        os.execv(executable, [executable] + [LOCAL_SCRIPT_PATH])
    else:
        # Если это не Windows, используем стандартный запуск
        os.execv(sys.executable, ['python'] + [LOCAL_SCRIPT_PATH])

# Ручка для получения версии агента
@app.route('/version', methods=['GET'])
def get_version():
    return jsonify({
        "current_version": CURRENT_VERSION
    })

# Ручка для обновления агента
@app.route('/update', methods=['GET'])
def update():
    if update_agent():
        restart_agent()
        return jsonify({"message": "Agent updated and restarted."})
    else:
        abort(500, description="Failed to update agent.")

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

@app.route('/metrics/cpu', methods=['GET'])
def get_cpu_metrics():
    metrics_data = get_metrics()
    return jsonify({"cpu": metrics_data["cpu"]})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
