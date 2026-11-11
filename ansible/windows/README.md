# Windows (win10 -> win11 prep + apps + AutoHotkey)

Ansible для Windows-хоста. Поки без реального хоста, запуск вручну (як laptop/wsl), в CI тільки lint. Підключення по OpenSSH через Tailscale.

Ролі:
- `win11_upgrade`: хук для апгрейду Win10 -> Win11 на непідтримуваному залізі.
- `choco_apps`: програми через Chocolatey.
- `autohotkey`: AutoHotkey v1 і скрипти в автозапуск.

Усі команди запускаються з `ansible/windows` (там `ansible.cfg` з інвентарем і `roles_path`):

```bash
cd ansible/windows
```

Якщо репозиторій лежить на диску Windows у WSL (`/mnt/c/...`), Ansible ігнорує `ansible.cfg` у директорії з відкритими правами. Клонуй репо у файлову систему WSL або задай `ANSIBLE_CONFIG=./ansible.cfg`.

## Роль `win11_upgrade`

Ставить хук [AveYo get11](https://github.com/AveYo/MediaCreationTool.bat/issues/203) ("Skip TPM Check on Dynamic Update", V13). Сам апгрейд **не запускається**, `setup.exe` ти запускаєш руками.

Як працює хук:
- IFEO-ключ `HKLM\...\Image File Execution Options\SetupHost.exe\0` з `Debugger = C:\Scripts\get11.cmd`.
- Коли Setup (з ISO або Windows Update) стартує `$WINDOWS.~BT\Sources\SetupHost.exe`, Windows замість нього запускає `get11.cmd`, який патчить перевірки TPM/CPU і сам запускає справжній Setup.
- Під час роботи ще пише `AllowUpgradesWithUnsupportedTPMorCPU=1` (MoSetup) і `DisableWUfBSafeguards=1` (політика WU).

Таски:
1. `present`: кладе `get11.cmd` в `C:\Scripts\get11.cmd` (`win_copy`, ідемпотентно за checksum, відновлює видалений або застарілий файл) і пише три IFEO-значення через `win_regedit` (`UseFilter`, `FilterFullPath`, `Debugger`). Це рівно те, що пише гілка `install` самого скрипта, але без запуску його інсталятора.
2. `absent`: видаляє IFEO-ключ `SetupHost.exe` і `C:\Scripts\get11.cmd`.
3. Якщо задано `win11_upgrade_iso_url`, качає ISO в `win11_upgrade_iso_dest` (async, не перекачує, якщо файл є). Без `win11_upgrade_iso_checksum` виводиться попередження.

### Змінні (`roles/win11_upgrade/defaults/main.yml`)

| Змінна | Дефолт | Опис |
| :--- | :--- | :--- |
| `win11_upgrade_hook_state` | `present` | `present` ставить хук, `absent` прибирає |
| `win11_upgrade_ifeo_key` | IFEO `SetupHost.exe` | ключ реєстру, де живе хук |
| `win11_upgrade_iso_url` | `""` | URL ISO, порожньо = не качати |
| `win11_upgrade_iso_checksum` | `""` | sha256, дуже бажано задавати |
| `win11_upgrade_iso_dest` | `C:\ISO\Win11.iso` | куди зберегти ISO |

### Як зробити апгрейд

1. Запустити роль (хук стоїть).
2. Змонтувати ISO: `Mount-DiskImage C:\ISO\Win11.iso`, або ПКМ, Mount.
3. Запустити `setup.exe` зі змонтованого диска. Важливо брати **справжній ISO** (не Upgrade Assistant) і з правильною мовою системи.
4. Setup піде як зазвичай, після перезавантаження логінишся в Win11.
5. Після апгрейду прибрати хук (`win11_upgrade_hook_state=absent`).

Бонус: щоб був відкат довше за 10 днів, в admin cmd:
```
DISM /Online /Set-OSUninstallWindow /Value:XX
```
де XX це кількість днів (наприклад 20).

## Роль `choco_apps`

Ставить програми через Chocolatey модулем `chocolatey.chocolatey.win_chocolatey` (сам встановлює choco, якщо його нема). Пакети в `roles/choco_apps/defaults/main.yml` по групах: `work`, `personal`, `hardware`, `devs`, `messengers`, `games`.

Як це працює:
- **Один виклик на пакет.** Збій одного пакета не зупиняє решту. Результат збирається у список `choco_apps_failed_packages`.
- **Підсумок у кінці плейбука.** Після ролей `post_task` у `site.yml` падає з переліком невстановлених пакетів. Так інші ролі й перезавантаження все одно відпрацьовують.
- **Один reboot в кінці ролей.** Якщо choco каже `reboot_required` (наприклад `docker-desktop`), роль лише сповіщає handler `Reboot windows`, він перезавантажує хост один раз після всіх ролей. Reboot посеред запуску не робиться, бо `choco-upgrade-all-at-startup` після старту ОС запускає `choco upgrade all` і наступний choco-таск упирався б у lock.

### Змінні

| Змінна | Дефолт | Опис |
| :--- | :--- | :--- |
| `choco_apps_groups` | `work, personal, devs, messengers` | які групи ставити (`hardware` і `games` вимкнені) |
| `choco_apps_state` | `present` | `present` ставить відсутні, `latest` оновлює все при кожному запуску |
| `choco_apps_extra_packages` | `[]` | додаткові пакети для конкретного хоста |
| `choco_apps_packages` | (див. defaults) | словник група -> список пакетів |

### Приклади

```bash
# тільки ігри
ansible-playbook site.yml --tags choco -e '{"choco_apps_groups": ["games"]}'
# всі групи
ansible-playbook site.yml --tags choco -e '{"choco_apps_groups": ["work","personal","hardware","devs","messengers","games"]}'
# оновити все
ansible-playbook site.yml --tags choco -e choco_apps_state=latest
```

### Нюанси choco_apps

- **`openssh` навмисно не в списку.** Choco-версія ставить свій sshd в `C:\Program Files\OpenSSH` і може зламати вбудований OpenSSH Server, на якому працює Ansible.
- **`choco-upgrade-all-at-startup`** створює задачу, яка оновлює всі choco-пакети при старті ОС. Версії можуть мінятись самі, без Ansible. Не комбінуй з `choco_apps_state=latest`, це подвійне оновлення.
- **`docker-desktop`** потребує WSL2/Hyper-V і перезавантаження. Роль перезавантажить хост наприкінці автоматично, тож не запускай її, коли на хості йде робота.
- **Перша інсталяція довга** (десятки пакетів, кожен окремим викликом). Повторний запуск доставить невстановлене, вже встановлене пропускається.
- **Зникли зі списку:** `javaruntime`, `windows-tweaker` (неактуальні), `grammarly`, `partition-assistant` (deprecated). Закоментовані пакети лишились у defaults як нотатки.
- **`autohotkey` винесений у роль `autohotkey`**, бо потрібна v1. Не додавай його назад у `choco_apps_packages`.
- Деякі пакети (`1password`, `icloud`, `googledrive`, `dropbox`) після встановлення потребують входу в акаунт вручну.

## Роль `autohotkey`

Ставить AutoHotkey **v1** (`autohotkey.install`, версія з `autohotkey_version`, пакет піниться) і кладе скрипти з `roles/autohotkey/files/` у загальну Startup-папку `C:\ProgramData\Microsoft\Windows\Start Menu\Programs\StartUp`. Скрипти запускаються при логіні будь-якого юзера.

Скрипти:
- `mac_win.ahk`: `LWin` стає `LCtrl`, `LAlt` вимкнений, `Win+A/C/V/S` дають `Ctrl+A/C/V/S` (розкладка як на Mac).
- `caps_lock_layout.ahk`: `CapsLock` перемикає розкладку (`Ctrl+Shift`), `Shift+CapsLock` це справжній CapsLock.

Таски:
1. Гарантує, що Chocolatey є (щоб роль працювала окремо, `--tags ahk`).
2. Через `win_chocolatey_facts` дивиться встановлену версію `autohotkey.install`. Якщо вона відрізняється від `autohotkey_version` (наприклад v2), сама видаляє `autohotkey` і `autohotkey.install`.
3. Ставить `autohotkey.install` потрібної версії з піном.
4. Копіює скрипти в Startup.

### Змінні (`roles/autohotkey/defaults/main.yml`)

| Змінна | Дефолт | Опис |
| :--- | :--- | :--- |
| `autohotkey_version` | `1.1.37.02` | версія `autohotkey.install` (скрипти під v1) |
| `autohotkey_scripts` | `mac_win.ahk`, `caps_lock_layout.ahk` | які скрипти розгорнути |
| `autohotkey_startup_dir` | `C:\ProgramData\...\StartUp` | куди класти |

### Нюанси autohotkey

- **v1, не v2.** Пакет `autohotkey` з choco тепер v2, а скрипти в синтаксисі v1 на ньому не працюватимуть. Роль сама замінить іншу версію на `autohotkey_version`. Версія `1.1.37.02` має бути в Chocolatey, якщо ні, зміни `autohotkey_version`.
- **Скрипти не запускаються одразу після деплою.** Роль йде по SSH, це не інтерактивна сесія, тож процес AHK не зʼявиться на робочому столі. Скрипти стартують при наступному логіні, тому зроби log off / log on (або reboot).
- **Список скриптів лише додається.** Якщо прибрати скрипт з `autohotkey_scripts`, роль його з Startup не видалить, прибери файл руками.
- **`.ahk` має бути асоційований з AHK v1.** Інсталятор це робить сам. Якщо поставити v1 і v2 разом, асоціація може вказувати не туди.
- **`mac_win.ahk`: `LWin::LCtrl` перехоплює Win.** Це змінює і системні `Win+...` (Win+R, Win+E тощо), вони стануть `Ctrl+R/E`. Хоткеї `#a/#c/#v/#s` у скрипті при цьому можуть ніколи не спрацьовувати, бо `LWin` вже переназначений. Скрипт взято як є, без правок.
- **UAC і elevated вікна.** AHK без прав адміна не діє у вікнах, що запущені від адміна (Диспетчер завдань, адмін-консоль).
- **Скрипти в Startup для всіх юзерів.** Мають діяти на кожного, хто логіниться на цей ПК.
- **`mac_win.ahk` збережений з BOM** (UTF-8), так AHK v1 коректно читає не-ASCII. Не пересохраняй у редакторі, який BOM прибирає.

## Підготовка

На control node (Linux/WSL):

```bash
cd ansible/windows
ansible-galaxy collection install -r requirements.yml
export WIN_USER=<windows user>   # інакше Administrator
```

`requirements.yml` містить нижні межі версій колекцій. Після встановлення можна зафіксувати точні версії.

Підключення: OpenSSH Server на Windows, юзер з групи Administrators (змінна середовища `WIN_USER`), той самий ключ, що для Pi (`/home/last/raspberry`), хост у Tailscale. Налаштування в `group_vars/all`, хост у `hosts` (розкоментувати і підставити імʼя з tailnet).

### Bootstrap SSH (один раз, руками на Windows)

Ansible ще нема як підключитись, тому це робиться локально.

1. Взяти публічний ключ на control node:
   ```bash
   ssh-keygen -y -f /home/last/raspberry
   ```
2. Встановити Tailscale на Windows і залогінитись (тег/доступ по ACL як для інших Pi).
3. Зберегти скрипт нижче як `ssh_setup.ps1` і запустити в **PowerShell від адміна**:
   ```powershell
   powershell -ExecutionPolicy Bypass -File .\ssh_setup.ps1 -PublicKey "ssh-ed25519 AAAA... comment"
   ```

```powershell
param(
    [Parameter(Mandatory)][string]$PublicKey
)
$ErrorActionPreference = 'Stop'

# 1. Install OpenSSH Server if missing
# (Get-WindowsCapability returns both Client and Server, so check the Server State)
$cap = Get-WindowsCapability -Online | Where-Object Name -like 'OpenSSH.Server*'
if ($cap.State -ne 'Installed') {
    Write-Output "OpenSSH Server is not installed. Installing..."
    Add-WindowsCapability -Online -Name 'OpenSSH.Server~~~~0.0.1.0' | Out-Null
} else {
    Write-Output "OpenSSH Server is already installed."
}

# 2. Start sshd and enable autostart
# (first start creates C:\ProgramData\ssh)
Set-Service -Name sshd -StartupType 'Automatic'
Start-Service sshd

# 3. Firewall rule (normally created by setup)
$rule = Get-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -ErrorAction SilentlyContinue
if ($null -eq $rule) {
    Write-Output "Firewall rule does not exist, creating it..."
    New-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -DisplayName 'OpenSSH Server (sshd)' `
        -Enabled True -Direction Inbound -Protocol TCP -Action Allow -LocalPort 22 | Out-Null
} else {
    Write-Output "Firewall rule 'OpenSSH-Server-In-TCP' exists."
}

# 4. Default shell = PowerShell (Ansible windows modules need it)
New-Item -Path HKLM:\SOFTWARE\OpenSSH -Force | Out-Null
New-ItemProperty -Path HKLM:\SOFTWARE\OpenSSH -Name DefaultShell `
    -Value 'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe' `
    -PropertyType String -Force | Out-Null

# 5. Key for Administrators group members
$keyFile = "$env:ProgramData\ssh\administrators_authorized_keys"
if (-not (Test-Path $keyFile)) { New-Item -Path $keyFile -ItemType File | Out-Null }
if (-not (Select-String -Path $keyFile -SimpleMatch $PublicKey -Quiet)) {
    Add-Content -Path $keyFile -Value $PublicKey -Encoding ascii
}
# sshd ignores the file if ACL is wider than SYSTEM + Administrators
icacls.exe $keyFile /inheritance:r /grant 'Administrators:F' /grant 'SYSTEM:F' | Out-Null

Restart-Service sshd
Get-Service sshd | Format-Table Name, Status, StartType
```

Перевірка з control node:

```bash
ssh -i /home/last/raspberry <user>@win10.tail72e9f.ts.net
ansible windows -m ansible.windows.win_ping
```

## Запуск

```bash
cd ansible/windows
# все
ansible-playbook site.yml
# тільки хук для апгрейду / тільки програми / тільки AutoHotkey
ansible-playbook site.yml --tags win11
ansible-playbook site.yml --tags choco
ansible-playbook site.yml --tags ahk
# прибрати хук після апгрейду
ansible-playbook site.yml --tags win11 -e win11_upgrade_hook_state=absent
# з завантаженням ISO
ansible-playbook site.yml --tags win11 -e win11_upgrade_iso_url=https://example/Win11.iso -e win11_upgrade_iso_checksum=<sha256>
```

## CI

- `.github/workflows/ansible.yml` деплоїть тільки Pi і тригериться лише на `ansible/raspberry/**`, правки в `ansible/windows` його не запускають.
- `.github/workflows/ansible_windows_lint.yml` на зміни в `ansible/windows/**` ганяє `ansible-playbook site.yml --syntax-check` і `ansible-lint --profile basic`. До хоста не підключається. Збій приходить у Telegram через `telegram_notify.yml`.

## Нюанси (win11_upgrade і SSH)

- **Тільки Win10 22H2+ та Win11 22H2+ ISO.** Скрипт перевіряє версію `SetupHost.exe` (`10.0.22xxx+`), для старих збірок хук нічого не робить.
- **`get11.cmd` це лише payload хука.** Роль його не запускає й не викликає його toggle. Не запускай його вручну без аргументів: у режимі setup він сам перемикає install/remove.
- **Легасі-чистка не відтворена.** Власний інсталятор скрипта ще прибирає старі хуки V1-V5 (WMI-фільтр, IFEO `vdsldr.exe`). Роль цього не робить, на чистій системі це не потрібно.
- **SSH-ключ.** Для юзерів з Administrators Windows читає `C:\ProgramData\ssh\administrators_authorized_keys`, а не `~\.ssh\authorized_keys`. Якщо ACL на файлі ширший за SYSTEM + Administrators, sshd мовчки ігнорує ключ (скрипт виставляє правильні ACL). Якщо на ключі є passphrase, потрібен ssh-agent.
- **Firewall і Tailscale.** Правило `OpenSSH-Server-In-TCP` відкриває 22 для всіх. Щоб пускати тільки tailnet, додай `-RemoteAddress 100.64.0.0/10` до правила.
- **Шлях `C:\Scripts\get11.cmd` зашитий у самому скрипті** (`%SystemDrive%\Scripts`), тому він не винесений у змінну.
- **Скрипт сторонній і лізе в реєстр.** Антивіруси/Defender можуть реагувати на IFEO-Debugger. Скрипт лежить у ролі як є, без змін (оновлювати вручну з апстриму).
- **Непідтримуване залізо.** Microsoft не гарантує оновлення/патчі для Win11 на непідтримуваному TPM/CPU. Windows Update може відмовитись ставити окремі апдейти.
- **Якщо Windows Update завис** (при апгрейді через WU), потрібен окремий `windows_update_refresh.bat` з того ж репо AveYo, тут його нема. Для Beta/Dev/Canary потрібен OfflineInsiderEnroll.
- **ISO.** Прямого стабільного лінку на ISO від Microsoft нема, URL задається свій. Задавай `win11_iso_checksum`, щоб не було битого завантаження. Файл займає ~5-6 ГБ, перевір місце на диску.
- **Ідемпотентність.** Повторний запуск при вже встановленому хуку нічого не змінює.
- **Очистка.** Хук має сенс лише на час апгрейду. Після нього краще прибрати (`win11_upgrade_hook_state=absent`).
