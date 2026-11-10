# Windows (win10 -> win11 prep)

Ansible для Windows-хоста. Поки без реального хоста і не в CI, запуск вручну (як laptop/wsl).

## Що робить роль `win11_upgrade`

Ставить хук [AveYo get11](https://github.com/AveYo/MediaCreationTool.bat/issues/203) ("Skip TPM Check on Dynamic Update", V13). Сам апгрейд **не запускається**, `setup.exe` ти запускаєш руками.

Як працює хук:
- IFEO-ключ `HKLM\...\Image File Execution Options\SetupHost.exe\0` з `Debugger = C:\Scripts\get11.cmd`.
- Коли Setup (з ISO або Windows Update) стартує `$WINDOWS.~BT\Sources\SetupHost.exe`, Windows замість нього запускає `get11.cmd`, який патчить перевірки TPM/CPU і сам запускає справжній Setup.
- Під час роботи ще пише `AllowUpgradesWithUnsupportedTPMorCPU=1` (MoSetup) і `DisableWUfBSafeguards=1` (політика WU).

Таски:
1. Дивиться, чи є IFEO-ключ (ідемпотентність).
2. Якщо треба змінити стан: копіює `get11.cmd` у `C:\Windows\Temp\get11_setup.cmd`, запускає його з `install` або `remove`, перевіряє ключ, прибирає тимчасову копію.
3. Якщо задано `win11_iso_url`, качає ISO в `win11_iso_dest` (не перекачує, якщо файл є).

## Змінні (`roles/win11_upgrade/defaults/main.yml`)

| Змінна | Дефолт | Опис |
| :--- | :--- | :--- |
| `win11_hook_state` | `present` | `present` ставить хук, `absent` прибирає |
| `win11_work_dir` | `C:\Windows\Temp` | куди тимчасово копіюється інсталятор |
| `win11_iso_url` | `""` | URL ISO, порожньо = не качати |
| `win11_iso_checksum` | `""` | sha256, опційно |
| `win11_iso_dest` | `C:\ISO\Win11.iso` | куди зберегти ISO |

## Підготовка

На control node (Linux/WSL):

```bash
ansible-galaxy collection install ansible.windows
pip install pywinrm        # тільки якщо WinRM
```

Підключення (поки не вирішено), розкоментувати в `group_vars/all` і додати хост у `hosts`:
- **WinRM** (`ansible_connection: winrm`): треба включений WinRM на хості (`winrm quickconfig`), HTTPS або NTLM. Пароль з env `WIN_PASSWORD`, не в git.
- **OpenSSH** (`ansible_connection: ssh`, `ansible_shell_type: powershell`): ставиться як Windows-фіча OpenSSH Server.

## Запуск

```bash
ansible-playbook -i ansible/windows/hosts ansible/windows/site.yml
# прибрати хук після апгрейду
ansible-playbook -i ansible/windows/hosts ansible/windows/site.yml -e win11_hook_state=absent
# з завантаженням ISO
ansible-playbook -i ansible/windows/hosts ansible/windows/site.yml -e win11_iso_url=https://example/Win11.iso -e win11_iso_checksum=<sha256>
```

## Як зробити апгрейд

1. Запустити роль (хук стоїть).
2. Мок ISO: `Mount-DiskImage C:\ISO\Win11.iso`, або ПКМ, Mount.
3. Запустити `setup.exe` зі змонтованого диска. Важливо брати **справжній ISO** (не Upgrade Assistant) і з правильною мовою системи.
4. Setup піде як зазвичай, після перезавантаження логінишся в Win11.
5. Після апгрейду прибрати хук (`win11_hook_state=absent`).

Бонус: щоб був відкат довше за 10 днів, в admin cmd:
```
DISM /Online /Set-OSUninstallWindow /Value:XX
```
де XX це кількість днів (наприклад 20).

## Нюанси

- **Тільки Win10 22H2+ та Win11 22H2+ ISO.** Скрипт перевіряє версію `SetupHost.exe` (`10.0.22xxx+`), для старих збірок хук нічого не робить.
- **Elevation.** `get11.cmd` вимагає адмін-прав. Якщо сесія не elevated (`fltmc` падає), скрипт спробує підняти себе через `runas` в інтерактивному вікні, і по WinRM/SSH це зависне або не спрацює. Виконуй під локальним Administrator або акаунтом з вимкненим UAC-filtering для remote. Роль перевіряє результат через `assert`, тож буде явна помилка.
- **Не запускай `get11.cmd` без аргументів вручну повторно:** без аргументів він працює як toggle (встановлено, то видалить). Роль завжди передає `install`/`remove`.
- **Шлях `C:\Scripts\get11.cmd` зашитий у самому скрипті** (`%SystemDrive%\Scripts`), тому він не винесений у змінну.
- **Скрипт сторонній і лізе в реєстр.** Антивіруси/Defender можуть реагувати на IFEO-Debugger. Скрипт лежить у ролі як є, без змін (оновлювати вручну з апстриму).
- **Непідтримуване залізло.** Microsoft не гарантує оновлення/патчі для Win11 на непідтримуваному TPM/CPU. Windows Update може відмовитись ставити окремі апдейти.
- **Якщо Windows Update завис** (при апгрейді через WU), потрібен окремий `windows_update_refresh.bat` з того ж репо AveYo, тут його нема. Для Beta/Dev/Canary потрібен OfflineInsiderEnroll.
- **ISO.** Прямого стабільного лінку на ISO від Microsoft нема, URL задається свій. Задавай `win11_iso_checksum`, щоб не було битого завантаження. Файл займає ~5-6 ГБ, перевір місце на диску.
- **Ідемпотентність.** Повторний запуск при вже встановленому хуку нічого не змінює.
- **Очистка.** Хук має сенс лише на час апгрейду. Після нього краще прибрати (`win11_hook_state=absent`).