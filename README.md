# Cardputer Quick Voice Notes

**Архив быстрых голосовых заметок одной кнопкой на M5Stack Cardputer-Adv.**

Обычная заметка в Telegram требует достать и разблокировать телефон, включить VPN, открыть Telegram и удерживать кнопку записи. Здесь достаточно нажать **BtnG0**, сказать заметку и нажать её ещё раз. Запись сохраняется сразу на microSD, а когда рядом доступна знакомая Wi-Fi-сеть и Mac, автоматически появляется голосовым сообщением в Telegram и получает расшифровку от SaluteSpeech.

Устройство работает как автономный диктофон: пишет честный PCM-звук на microSD и не зависит от Wi-Fi во время записи. Локальный macOS-agent принимает закрытые фрагменты с возобновлением после обрыва, проверяет WAV, конвертирует его в OGG/Opus и отправляет обычным Telegram voice от пользовательской сессии в выбранную группу. Основной сценарий не использует Telegram Bot API.

## Быстрый сценарий

1. Нажмите **BtnG0** — начинается запись, на экране виден красный `REC`.
2. Скажите заметку любой длины; длинные записи безопасно делятся на пятиминутные фрагменты.
3. Нажмите **BtnG0** ещё раз — WAV закрывается и ставится в очередь отправки.
4. В знакомой сети Mac принимает запись, Telegram показывает её как voice, SaluteSpeech присылает текст.

Телефон, открытый Telegram и ручной запуск VPN для создания заметки не нужны.

Оба критических пути проверены на реальном устройстве и в группе «Заметки»: 60-секундный WAV с ES8311 прошёл `ffprobe`, Telegram message `240609` был отправлен через Telethon с `voice_note=True`, а @smartspeech_sber_bot ответил расшифровкой (`240610`). Подробности находятся в [docs/spikes.md](docs/spikes.md).

## Как это работает

```text
BtnG0 -> ES8311 -> 16 kHz mono PCM -> microSD *.wav.part
                                      | stop / каждые 5 минут
                                      v
                                  закрытый *.wav
                                      |
                    Wi-Fi + HTTP /v1 resumable upload
                                      v
                  Mac incoming -> ready -> OGG/Opus -> Telethon
                                      |                    |
                                 durable ACK          группа «Заметки»
                                      |                    |
                             local synced journal     SaluteSpeech text
```

На Cardputer состояния: `IDLE`, `RECORDING`, `STOPPING`, `SYNCING`, `SYNC ERROR`, `SYNC CANCELLED`, `SD ERROR`, `LOW SPACE`. На Mac SQLite хранит `receiving`, `ready`, `converting`, `converted`, `sending`, `sent`, `failed`. Все переходы файлов выполняются через `.part` и атомарное переименование.

## Управление

- **BtnG0**: начать запись. Повторное нажатие после защитных 1,5 секунды сразу останавливает запись, закрывает WAV и запускает синхронизацию. Ждать 60 секунд не нужно.
- **BtnG0 во время `SYNCING`**: отменить текущую синхронизацию. `Fn + \`` (`Esc`) работает так же. Запись остаётся на карте, частично принятый файл остаётся на Mac и продолжится с сохранённого offset при следующей попытке.
- **S**: вручную запустить синхронизацию закрытых записей.

Во время записи экран показывает красный `REC`, длительность, номер фрагмента, свободное место и заряд. Через 15 секунд подсветка приглушается, но индикатор записи остаётся видимым. Скрытая и удалённо включаемая запись отсутствует.

## Проверенное окружение

- M5Stack Cardputer-Adv, ESP32-S3 rev 0.2, 8 MB flash, ES8311
- microSD 32 GB с FAT32; прошивка карту не форматирует
- macOS 26.3.1 (arm64)
- Python 3.14.6 в локальных `.venv` и `.venv-platformio`
- PlatformIO Core 6.2.0, Espressif32 6.7.0, Arduino-ESP32 2.0.16
- M5Cardputer 1.1.1 commit `f1392858...`, M5Unified 0.2.22 commit `e79eb6e...`
- ffmpeg/ffprobe 8.1.2 из Homebrew
- Telethon 1.45.0

## Установка с чистого Mac

Понадобятся Xcode Command Line Tools, Python 3.12+ и Homebrew с `ffmpeg`. Системный Python проект не изменяет.

```bash
cd /путь/к/cardputer-recorder
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e 'mac-agent[test]'

python3 -m venv .venv-platformio
.venv-platformio/bin/python -m pip install platformio==6.2.0
```

Если `ffmpeg` отсутствует и Homebrew уже установлен:

```bash
brew install ffmpeg
```

### microSD

Используйте исправную FAT32-карту 32 GB. Вставьте её при выключенном Cardputer. Прошивка только проверяет карту и свободное место; автоматического форматирования нет. Несинхронизированные записи никогда не удаляются. При нехватке места можно удалить только старейшие WAV, имеющие durable ACK Mac и запись в append-only журнале `/sync-acks.log`.

PCM 16 кГц × 16 бит × mono занимает 32 000 байт/с, около 115,2 MB/час. Номинальные 32 GB дают примерно 277,8 часа; реально проверенная карта сообщила 31 902 269 440 свободных байт, около 276,9 часа до резерва 256 MiB.

### Telegram

1. Создайте приватную группу и добавьте туда свой аккаунт и `@smartspeech_sber_bot`.
2. Отправьте ручное голосовое и убедитесь, что бот отвечает расшифровкой.
3. На `https://my.telegram.org` откройте **API development tools**. `URL` можно оставить пустым; если форма требует значение, укажите локальный адрес вроде `http://localhost`. Платформа: **Desktop**. Получите `api_id` и `api_hash`.
4. Запустите `.venv/bin/python scripts/telegram_login.py`. Вводите телефон, API credentials, одноразовый код и 2FA только в локальном Terminal. Они не должны попадать в чат или shell history.
5. Выберите нужную группу. Перед первой реальной отправкой скрипт показывает её название и ID, после чего требуется явное подтверждение.

`.env`, `*.session`, Wi-Fi-пароль, токены, записи, логи и flash-backup исключены из git.

### Локальная конфигурация

После Telegram-login выполните:

```bash
.venv/bin/python scripts/configure_local.py
```

Скрипт скрыто запросит SSID и пароль, сгенерирует общий device token, определит адрес физического Wi-Fi-интерфейса Mac даже при активном VPN и создаст два файла с правами `0600`: `.env` и игнорируемый `firmware/include/local_config.h`. Секреты не печатаются. Agent привязывается к этому LAN-адресу.

Проверка конфигурации и локального хранилища:

```bash
.venv/bin/cardputer-agent --env .env check-config
.venv/bin/cardputer-agent --env .env status
```

### Сборка и прошивка Cardputer-Adv

```bash
.venv-platformio/bin/pio run -d firmware
```

До первой записи flash нужно определить порт по VID/PID и ESP32-S3 bootloader, затем сохранить полный backup. Не выбирайте первый `/dev/cu.*` вслепую. Проект не использует `erase_flash`, не форматирует SD и не меняет factory partition table. Для уже проверенного устройства приложение записывается только по адресу `0x10000`:

```bash
.venv-platformio/bin/python ~/.platformio/packages/tool-esptoolpy/esptool.py \
  --chip esp32s3 --port /dev/cu.usbmodem1101 --baud 921600 \
  write_flash 0x10000 firmware/.pio/build/cardputer_adv_recorder/firmware.bin
```

После reset serial log должен содержать точные `FIRMWARE_VERSION`, `BUILD_TIMESTAMP`, `M5_BOARD_ID=24` и `CARDPUTER_ADV_READY=1`. Команда восстановления и полный factory backup лежат локально в `backups/`; backup не включает microSD.

Если bootloader не отвечает: переведите боковой выключатель в OFF, удерживайте **G0**, подключите USB-C data cable и отпустите **G0**.

### macOS-agent

Ручной запуск для диагностики:

```bash
.venv/bin/cardputer-agent --env .env serve
```

Установка и запуск LaunchAgent после входа пользователя:

```bash
.venv/bin/python scripts/install_launch_agent.py
launchctl print gui/$(id -u)/com.ganzoliki.cardputer-recorder
```

Удаление сервиса без удаления записей:

```bash
launchctl bootout gui/$(id -u) "$HOME/Library/LaunchAgents/com.ganzoliki.cardputer-recorder.plist"
rm "$HOME/Library/LaunchAgents/com.ganzoliki.cardputer-recorder.plist"
```

Операционные команды:

```bash
.venv/bin/cardputer-agent --env .env status
.venv/bin/cardputer-agent --env .env list-pending
.venv/bin/cardputer-agent --env .env retry-failed
```

Данные имеют права `0700` и лежат в `data/agent/{incoming,ready,sent,failed,logs}`. Исходные WAV после отправки сохраняются в `sent/`; retention на Mac выключен. Access log HTTP отключён, Telegram-текст и токены не пишутся. Технический `logs/agent.log` имеет права `0600`, ротируется по 5 MiB и хранит не более трёх архивов.

## Тесты

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv-platformio/bin/pio run -d firmware
```

Автотесты проверяют WAV header и аварийное восстановление, симуляцию двухчасовой записи и 24 ротаций, загрузки с остановкой на 30/70/99%, восстановление offset, неверный токен, path traversal, idempotency, строгий порядок очереди, рестарт между Telegram send и фиксацией файлов, отсутствие дубля после найденного Telegram message ID и настоящий ffmpeg/ffprobe OGG/Opus pipeline.

Аппаратный двухчасовой тест: полностью зарядить устройство, начать BtnG0, говорить/создавать контрольный звук около границ каждых пяти минут, через два часа остановить BtnG0; проверить 24 последовательных WAV через ffprobe и прослушать стыки. Отдельно во время нового фрагмента отключить питание, включить устройство и проверить `.recovered.wav`, не затрагивая предыдущие файлы.

## Диагностика

- `SYNC ERROR / Known Wi-Fi unavailable`: проверить локальный SSID/пароль и диапазон Wi-Fi.
- `Mac agent not found`: Mac должен бодрствовать; проверить LaunchAgent, LAN IP в `local_config.h` и отсутствие client isolation в роутере.
- `SYNC CANCELLED`: нажать `S` позже; передача продолжится с сохранённого offset. После сетевой ошибки автоматический retry использует экспоненциальный backoff с jitter до десяти минут.
- `failed` на Mac: посмотреть краткую техническую ошибку в локальном логе, устранить причину и выполнить `retry-failed`.
- `LOW SPACE`: скопировать/архивировать записи. Прошивка не удалит ни один файл без durable ACK.
- После обновления прошивки записи на microSD и Mac не удаляются; не запускайте `erase_flash` и не меняйте partition table.

Mac в sleep или выключенном состоянии не принимает файлы. Cardputer хранит очередь на microSD и повторяет позже. Реальная автономность зависит от яркости, качества Wi-Fi, microSD и возраста батареи; короткие Wi-Fi-сеансы выполняются реже от батареи и чаще при USB-питании.

Получайте согласие людей на запись и соблюдайте местное законодательство. После отправки аудио хранится не только локально, но и в Telegram/SaluteSpeech. LAN API нельзя выставлять через port forwarding, UPnP или публичный туннель.
