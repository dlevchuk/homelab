# Стек проєкту

Знімок за репозиторієм і доступними чатами, 2026-11-17. Перед змінами перечитай джерела. RAM, моделі блоків живлення, накопичувачів і розрядність усіх ОС тут не встановлені.

## Hardware і розподіл сервісів

| Inventory host | Плата / призначення | Конфігурація |
| --- | --- | --- |
| `rpi-pi4` | Raspberry Pi 4, хаб | SSH 7722; Homepage, Uptime Kuma, Portainer/Dozzle/Beszel hubs; Changedetection і wishlist sync |
| `rpi-pi5` | Raspberry Pi 5, застосунки | SSH 22; Glance на 3005 замість Homepage; Home Assistant, Hermes; Portainer/Dozzle/Beszel agents; керована крива вентилятора |
| `rpi-zero` | Raspberry Pi Zero 2 W, камера/сенсори | SSH 22; thermal camera, BME280, MH-FMD, Beszel agent; `common` і `docker_apps` пропускаються |

Адреси — `ansible/raspberry/hosts`; налаштування — `group_vars/raspberry*.yml`. Docker defaults: користувач `pi`, UID/GID 1000, `/home/pi/docker`, timezone `Europe/Kiev`. Hermes усередині контейнера: UID/GID 10000 і state `/opt/data`.

Pi 5 описаний як Raspberry Pi OS Trixie; інші версії ОС перевіряй. `enable_wifi: true` у Pi 4/5. Наявність NetworkManager на Zero сама по собі не вмикає роль Wi-Fi.

## Поточна розводка Zero

Джерело: `docs/thermal-camera.md`, `roles/thermal_cam/files/thermal_web.py`.

| Пристрій / контакт | Фізичний пін | BCM / функція |
| --- | --- | --- |
| BME280 VCC | 17 | 3.3 V |
| BME280 GND | 9 | GND |
| BME280 SCL | 23 | GPIO11 / SPI0 SCLK |
| BME280 SDA | 19 | GPIO10 / SPI0 MOSI |
| BME280 SDO | 21 | GPIO9 / SPI0 MISO |
| BME280 CSB | 24 | GPIO8 / SPI0 CE0 |
| MH-FMD VCC | 4 | 5 V за поточною документацією |
| MH-FMD GND | 6 | GND |
| MH-FMD SIG | 12 | GPIO18 / PWM |

BME280: SPI, chip ID `0x60`, лише 3.3 V. Написи SCL/SDA на модулі не означають поточне I²C-підключення. Старий чат містив іншу пораду живлення бузера; перед перепідключенням звір фактичний модуль і специфікацію. GPIO Pi не є 5 V tolerant.

Камера: V4L2 `/dev/video0`, ffmpeg, HTTP MJPEG. README називає MLX90640, але це не доказ I²C інтерфейсу: перевір фактичну модель/пристрій перед зміною драйвера. DHT11 та SR505 згадувалися в ранніх експериментах; не вважай їх активними без підтвердження.

## Software і операції

- Speedtest Tracker (`lscr.io/linuxserver/speedtest-tracker`): стандартний логін `admin@example.com`, пароль `password`, якщо їх не змінювали в застосунку. Це публічні початкові дані, а не підтверджені поточні облікові дані Pi. У проєкті вебпорт `8765`; конфігурація — `ansible/raspberry/roles/docker_apps/tasks/docker_speedtest.yml`. Джерело: [LinuxServer Application Setup](https://docs.linuxserver.io/images/docker-speedtest-tracker/#application-setup), перевірено 2026-11-17.

- Ansible: `ansible/raspberry/site.yml`; ролі `logging`, `wifi`, `common`, `docker_apps`, `thermal_cam`, `beszel_agent`, `outage_notify`, `space_cowboy`.
- Docker: `community.docker.docker_container`; Syncthing, Speedtest, Glances, Watchtower та інші застосунки залежать від defaults + host overrides. Наявність task-файла не означає enabled. Music Assistant вимкнений у Pi 5 попри коментар про його розташування.
- NetworkManager/nmcli, fallback AP `10.42.0.1/24`, секрет `WIFI_CONFIG`; boot grace 3 хв, timer щохвилини, retry клієнтських мереж кожні 5 хв за відсутності AP-клієнтів. Роль не мігрує dhcpcd автоматично; зберігає netplan-generated профілі.
- Tailscale: віддалений SSH, доступ CI, вебінтерфейси. Hermes bind loopback + Tailscale Serve. Browser-to-host і container-to-host — різні мережеві шляхи.
- Thermal: Python, ffmpeg/V4L2, `python3-spidev`, `python3-gpiozero`, `python3-lgpio`, SQLite WAL, systemd services/timers. BME280 reader не потребує Blinka/venv/pip.
- SQLite локально, щоденна синхронізація Supabase через stdlib/PostgREST; cursor після успішного batch, повторюваний upsert. Актуальна схема — в коді/документації.
- Telegram: сповіщення; окрема personal Telegram інтеграція Hermes. Прямий API тест не доводить роботу через Hermes.
- Backup: Docker archives у Syncthing, за defaults 3 копії, перевірка строку щодня й після boot; для консистентних баз defaults зупиняють контейнери. Враховуй перерву сервісів.
- Logging defaults: journald 100 MiB / 7 днів; rsyslog daily / 7 compressed archives / threshold 10 MiB; Docker 3 × 10 MiB.
- CI: Python tests за шляхами, Windows Ansible lint, security checks; formatting/secrets через pre-commit.ci. Автодеплой лише змін `ansible/raspberry/**` на `main`; решта хостів вручну. Зміна скіла не запускає Pi deployment.
