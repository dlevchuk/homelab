# Windows (win10 -> win11 prep + apps + AutoHotkey)

Ansible для Windows-хоста. Поки без реального хоста і не в CI, запуск вручну (як laptop/wsl). Підключення по OpenSSH через Tailscale.

Ролі:
- `win11_upgrade`: хук для апгрейду Win10 -> Win11 на непідтримуваному залізі.
- `choco_apps`: програми через Chocolatey.
- `autohotkey`: AutoHotkey v1 і скрипти в автозапуск.

## Роль `win11_upgrade`

Ставить хук [AveYo get11](https://github.com/AveYo/MediaCreationTool.bat/issues/203) ("Skip TPM Check on Dynamic Update", V13). Сам апгрейд **не запускається**, `setup.exe` ти запускаєш руками.

Як працює хук:
- IFEO-ключ `HKLM\...\Image File Execution Options\SetupHost.exe\0` з `Debugger = C:\Scripts\get11.cmd`.
- Коли Setup (з ISO або Windows Update) стартує `$WINDOWS.~BT\Sources\SetupHost.exe`, Windows замість нього запускає `get11.cmd`, який патчить перевірки TPM/CPU і сам запускає справжній Setup.
- Під час роботи ще пише `AllowUpgradesWithUnsupportedTPMorCPU=1` (MoSetup) і `DisableWUfBSafeguards=1` (політика WU).

Таски:
1. Дивиться, чи є IFEO-ключ (ідемпотентність).
2. Якщо треба змінити стан: копіює `get11.cmd` у `C:\Windows\Temp\get11_setup.cmd`, запускає його з `install` або `remove`, перевіряє ключ, прибирає тимчасову копію.
3. Якщо задано `win11_iso_url`, качає ISO в `win11_iso_dest` (не перекачує, якщо файл є).

### Змінні (`roles/win11_upgrade/defaults/main.yml`)

| Змінна | Дефолт | Опис |
| :--- | :--- | :--- |
| `win11_hook_state` | `present` | `present` ставить хук, `absent` прибирає |
| `win11_work_dir` | `C:\Windows\Temp` | куди тимчасово копіюється інсталятор |
| `win11_iso_url` | `""` | URL ISO, порожньо = не качати |
| `win11_iso_checksum` | `""` | sha256, опційно |
| `win11_iso_dest` | `C:\ISO\Win11.iso` | куди зберегти ISO |

### Як зробити апгрейд

1. Запустити роль (хук стоїть).
2. Змонтувати ISO: `Mount-DiskImage C:\ISO\Win11.iso`, або ПКМ, Mount.
3. Запустити `setup.exe` зі змонтованого диска. Важливо брати **справжній ISO** (не Upgrade Assistant) і з правильною мовою системи.
4. Setup піде як зазвичай, після перезавантаження логінишся в Win11.
5. Після апгрейду прибрати хук (`win11_hook_state=absent`).

Бонус: щоб був відкат довше за 10 днів, в admin cmd:
```
DISM /Online /Set-OSUninstallWindow /Value:XX
```
де XX це кількість днів (наприклад 20).

## Роль `choco_apps`

Ставить програми через Chocolatey модулем `chocolatey.chocolatey.win_chocolatey` (сам встановлює choco, якщо його нема). Після інсталяції, якщо choco каже `reboot_required` (наприклад `docker-desktop`), робиться автоматичний `win_reboot`.

Пакети в `roles/choco_apps/defaults/main.yml` по групах: `work`, `personal`, `hardware`, `devs`, `messengers`, `games`.

### Змінні

| Змінна | Дефолт | Опис |
| :--- | :--- | :--- |
| `choco_groups` | `work, personal, devs, messengers` | які групи ставити (`hardware` і `games` вимкнені) |
| `choco_state` | `present` | `present` ставить відсутні, `latest` оновлює все при кожному запуску |
| `choco_extra_packages` | `[]` | додаткові пакети для конкретного хоста |
| `choco_packages` | (див. defaults) | словник група -> список пакетів |

### Приклади

```bash
# тільки ігри
ansible-playbook -i ansible/windows/hosts ansible/windows/site.yml --tags choco -e '{"choco_groups": ["games"]}'
# всі групи
ansible-playbook -i ansible/windows/hosts ansible/windows/site.yml --tags choco -e '{"choco_groups": ["work","personal","hardware","devs","messengers","games"]}'
# оновити все
ansible-playbook -i ansible/windows/hosts ansible/windows/site.yml --tags choco -e choco_state=latest
```

### Нюанси choco_apps

- **`openssh` навмисно не в списку.** Choco-версія ставить свій sshd в `C:\Program Files\OpenSSH` і може зламати вбудований OpenSSH Server, на якому працює Ansible.
- **`choco-upgrade-all-at-startup`** створює задачу, яка оновлює всі choco-пакети при старті ОС. Версії можуть мінятись самі, без Ansible. Не комбінуй з `choco_state=latest`, це подвійне оновлення.
- **`docker-desktop`** потребує WSL2/Hyper-V і перезавантаження. Роль перезавантажить хост автоматично, тож не запускай її в момент, коли на хості йде робота.
- **Перша інсталяція довга** (десятки пакетів). Якщо один пакет впаде, модуль поверне помилку для всього таска, повторний запуск доставить решту (вже встановлені пропускаються).
- **Зникли з списку:** `javaruntime`, `windows-tweaker` (неактуальні), `grammarly`, `partition-assistant` (deprecated). Закоментовані пакети лишились у defaults як нотатки.
- **`autohotkey` винесений у роль `autohotkey`**, бо потрібна v1 (див. нижче). Не додавай його назад у `choco_packages`.
- Деякі пакети (`1password`, `icloud`, `googledrive`, `dropbox`) після встановлення потребують входу в акаунт вручну.

## Роль `autohotkey`

Ставить AutoHotkey **v1** (`autohotkey.install`, версія з `autohotkey_version`, пакет піниться) і кладе скрипти з `roles/autohotkey/files/` у загальну Startup-папку `C:\ProgramData\Microsoft\Windows\Start Menu\Programs\StartUp`. Скрипти запускаються при логіні будь-якого юзера.

Скрипти:
- `mac_win.ahk`: `LWin` стає `LCtrl`, `LAlt` вимкнений, `Win+A/C/V/S` дають `Ctrl+A/C/V/S` (розкладка як на Mac).
- `caps_lock_layout.ahk`: `CapsLock` перемикає розкладку (`Ctrl+Shift`), `Shift+CapsLock` це справжній CapsLock.

### Змінні (`roles/autohotkey/defaults/main.yml`)

| Змінна | Дефолт | Опис |
| :--- | :--- | :--- |
| `autohotkey_version` | `1.1.37.02` | версія `autohotkey.install` (скрипти під v1) |
| `autohotkey_scripts` | `mac_win.ahk`, `caps_lock_layout.ahk` | які скрипти розгорнути |
| `autohotkey_all_scripts` | обидва | усі відомі ролі скрипти, ті, що не в `autohotkey_scripts`, видаляються |
| `autohotkey_startup_dir` | `C:\ProgramData\...\StartUp` | куди класти |
| `autohotkey_reinstall` | `false` | `true` спочатку видаляє `autohotkey` і `autohotkey.install` |

### Нюанси autohotkey

- **v1, не v2.** Пакет `autohotkey` з choco тепер v2, а скрипти в синтаксисі v1 на ньому не працюватимуть. Якщо раніше на хості вже стояла v2 (наприклад від `choco_apps`), запусти один раз з `-e autohotkey_reinstall=true`. Версія `1.1.37.02` має бути в Chocolatey, якщо ні, зміни `autohotkey_version`.
- **Скрипти не запускаються одразу після деплою.** Роль йде по SSH, це не інтерактивна сесія, тож процес AHK не зʼявиться на робочому столі. Скрипти стартують при наступному логіні, тому зроби log off / log on (або reboot).
- **`.ahk` має бути асоційований з AHK v1.** Інсталятор це робить сам. Якщо поставити v1 і v2 разом, асоціація може вказувати не туди.
- **`mac_win.ahk`: `LWin::LCtrl` перехоплює Win.** Це змінює і системні `Win+...` (Win+R, Win+E тощо), вони стануть `Ctrl+R/E`. Хоткеї `#a/#c/#v/#s` у скрипті при цьому можуть ніколи не спрацьовувати, бо `LWin` вже переназначений. Скрипт взято як є, без правок.
- **UAC і elevated вікна.** AHK без прав адміна не діє у вікнах, що запущені від адміна (Диспетчер завдань, адмін-консоль).
- **Скрипти в Startup для всіх юзерів.** Мають діяти на кожного, хто логіниться на цей ПК. Для одного юзера потрібна окрема змінна під його Startup.
- **`mac_win.ahk` збережений з BOM** (UTF-8), так AHK v1 коректно читає не-ASCII. Не пересохраняй у редакторі, який BOM прибирає.

## Підготовка

На control node (Linux/WSL):

```bash
ansible-galaxy collection install ansible.windows chocolatey.chocolatey
```

Підключення: OpenSSH Server на Windows, юзер з групи Administrators, той самий ключ, що для Pi (`/home/last/raspberry`), хост у Tailscale. Налаштування в `group_vars/all`, хост у `hosts` (розкоментувати і підставити імʼя з tailnet).

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
ansible windows -i ansible/windows/hosts -m ansible.windows.win_ping
```

## Запуск

```bash
# все
ansible-playbook -i ansible/windows/hosts ansible/windows/site.yml
# тільки хук для апгрейду / тільки програми / тільки AutoHotkey
ansible-playbook -i ansible/windows/hosts ansible/windows/site.yml --tags win11
ansible-playbook -i ansible/windows/hosts ansible/windows/site.yml --tags choco
ansible-playbook -i ansible/windows/hosts ansible/windows/site.yml --tags ahk
# прибрати хук після апгрейду
ansible-playbook -i ansible/windows/hosts ansible/windows/site.yml --tags win11 -e win11_hook_state=absent
# з завантаженням ISO
ansible-playbook -i ansible/windows/hosts ansible/windows/site.yml --tags win11 -e win11_iso_url=https://example/Win11.iso -e win11_iso_checksum=<sha256>
```

## Нюанси (win11_upgrade і SSH)

- **Тільки Win10 22H2+ та Win11 22H2+ ISO.** Скрипт перевіряє версію `SetupHost.exe` (`10.0.22xxx+`), для старих збірок хук нічого не робить.
- **Elevation.** `get11.cmd` вимагає адмін-прав. По SSH юзер з групи Administrators отримує повний токен, тож `fltmc` проходить. З не-адмін юзером скрипт спробує підняти себе через `runas` в інтерактивному вікні і зависне. Роль перевіряє результат через `assert`, тож буде явна помилка.
- **SSH-ключ.** Для юзерів з Administrators Windows читає `C:\ProgramData\ssh\administrators_authorized_keys`, а не `~\.ssh\authorized_keys`. Якщо ACL на файлі ширший за SYSTEM + Administrators, sshd мовчки ігнорує ключ (скрипт виставляє правильні ACL). Якщо на ключі є passphrase, потрібен ssh-agent.
- **Firewall і Tailscale.** Правило `OpenSSH-Server-In-TCP` відкриває 22 для всіх. Щоб пускати тільки tailnet, додай `-RemoteAddress 100.64.0.0/10` до правила.
- **Не запускай `get11.cmd` без аргументів вручну повторно:** без аргументів він працює як toggle (встановлено, то видалить). Роль завжди передає `install`/`remove`.
- **Шлях `C:\Scripts\get11.cmd` зашитий у самому скрипті** (`%SystemDrive%\Scripts`), тому він не винесений у змінну.
- **Скрипт сторонній і лізе в реєстр.** Антивіруси/Defender можуть реагувати на IFEO-Debugger. Скрипт лежить у ролі як є, без змін (оновлювати вручну з апстриму).
- **Непідтримуване залізо.** Microsoft не гарантує оновлення/патчі для Win11 на непідтримуваному TPM/CPU. Windows Update може відмовитись ставити окремі апдейти.
- **Якщо Windows Update завис** (при апгрейді через WU), потрібен окремий `windows_update_refresh.bat` з того ж репо AveYo, тут його нема. Для Beta/Dev/Canary потрібен OfflineInsiderEnroll.
- **ISO.** Прямого стабільного лінку на ISO від Microsoft нема, URL задається свій. Задавай `win11_iso_checksum`, щоб не було битого завантаження. Файл займає ~5-6 ГБ, перевір місце на диску.
- **Ідемпотентність.** Повторний запуск при вже встановленому хуку нічого не змінює.
- **Очистка.** Хук має сенс лише на час апгрейду. Після нього краще прибрати (`win11_hook_state=absent`).