import datetime
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time

# MCDR Command & Class
from mcdreforged.api.command import Literal, SimpleCommandBuilder, Text
from mcdreforged.api.decorator import new_thread
from mcdreforged.api.rcon import RconConnection
from mcdreforged.api.rtext import RColor, RText, RTextList
from mcdreforged.mcdr_server import ServerInterface

# Initalize Start
platform = sys.platform
Interface = None

if sys.platform == "win32":
    MCDR_Command = "python -m mcdreforged"
else:
    MCDR_Command = "python3 -m mcdreforged"

PLUGIN_METADATA = {
    "id": "mirror_server_reforged",
    "version": "1.0.8-alpha.1",
    "name": "MirrorServerReforged",
    "description": "A reforged version of [MCDR-Mirror-Server](https://github.com/GamerNoTitle/MCDR-Mirror-Server), which is a plugin for MCDR-Reforged 2.6.0+.",
    "author": "GamerNoTitle",
    "link": "https://github.com/EMUnion/MirrorServerReforged",
    "dependencies": {"mcdreforged": ">=2.6.0"},
}

config = {
    "world": ["world"],
    "command": MCDR_Command,
    "rcon": {
        "enable": False,
        "host": "localhost",
        "port": 25575,
        "password": "password",
    },
    "source": "./server",
    "target": "./Mirror/server",
}

help_msg = """{:=^50}
§b!!msr help §r- §6显示帮助信息
§b!!msr sync §r- §6同步服务器地图至镜像
§b!!msr reload §r- §6重载配置文件
§b!!msr start §r- §6启动镜像服务器
§b!!msr stop §r- §6关闭镜像服务器（需要开启Rcon）
§b!!msr init §r- §6初始化镜像服务器（仅MCDR类服务器可用）
{:=^50}""".format(
    " §b[MirrorServerReforged] 帮助信息 §r",
    " §b[MirrorServerReforged] Version: {} §r".format(PLUGIN_METADATA["version"]),
)
# §b!!msr status §r- §6查看镜像服务器状态


# Started = False  # Mirror server status
MCDR = False  # MCDR mode controller
path = os.getcwd()
syncFlag = False
# Initalize End

# The longest time (in second) to wait for the mirror server to be fully stopped
# before telling the command source that the shutdown result is unknown
STOP_WAIT_TIMEOUT = 120
# The interval (in second) between two mirror server shutdown checks
STOP_WAIT_INTERVAL = 1
# The longest time (in second) to wait for the mirror server to finish starting up
# before telling the players that the startup result is unknown
START_WAIT_TIMEOUT = 300
# The interval (in second) between two mirror server startup checks
START_WAIT_INTERVAL = 2

# The result of WaitForMirrorStart
MIRROR_START_READY = "ready"  # the mirror server finished starting up
MIRROR_START_EXITED = "exited"  # the mirror server process died before it was ready
MIRROR_START_TIMEOUT = "timeout"  # still not ready when the wait timed out
MIRROR_START_UNKNOWN = "unknown"  # no usable readiness probe is available


def InitalizeOnFirstRun():
    if os.path.exists("./Mirror/MCDReforged.py") or "mcdreforged" in config["command"]:
        global MCDR
        MCDR = True  # Turn on MCDR mode
    if not os.path.exists("./Mirror"):
        print(
            "[MirrorServerReforged] 看起来你是第一次运行本插件？我们将会为您进行首次运行的初始化"
        )
        print("[MirrorServerReforged] 正在创建镜像文件夹……")
        if (
            MCDR
        ):  # MCDR mode on, create Mirror folder and a server folder in Mirror folder
            print(
                "[MirrorServerReforged] 检测到MCDR，我们将会按照MCDR的目录结构创建文件夹"
            )
            try:
                os.makedirs("./Mirror")
            except:
                print("[MirrorServerReforged] Mirror文件夹已存在！")
            os.makedirs("./Mirror/server")
            os.chdir("Mirror")
            # Create MCDR dictionary structure
            os.system("python3 -m mcdreforged init")
            os.makedirs("./server/world")
            os.chdir(path)
        else:  # MCDR mode off, turn into legacy mode. Like Vanilla, Bukkit, Waterfalls and so on.
            print(
                "[MirrorServerReforged] 未检测到MCDR，我们将会按照普通服务器的目录结构创建文件夹"
            )
            try:
                os.makedirs("./Mirror")
            except:
                print("[MirrorServerReforged] Mirror文件夹已存在！")
            for world in config["world"]:
                os.makedirs("./Mirror/{}".format(world))
        print("[MirrorServerReforged] 初始化完成！")


def CreateConfig():
    print("[MirrorServerReforged] 正在创建配置文件……")
    global config
    with open("./config/MirrorServerReforged.json", "w", encoding="utf-8") as f:
        f.write(json.dumps(config, indent=2, separators=(",", ":"), ensure_ascii=False))
        f.close()


def RconInit(host, port, password):
    Rcon = RconConnection(host, port, password)
    return Rcon


def LoadConfig():
    print("[MirrorServerReforged] 正在加载配置文件……")
    global config
    with open("./config/MirrorServerReforged.json", "r", encoding="utf-8") as f:
        config = json.load(f)
    if "source" not in config:
        config["source"] = "./server"
    if "target" not in config:
        config["target"] = "./Mirror/server"
    CreateConfig()


def Broadcast(InterFace, message, color=RColor.gold):
    """Broadcast without putting legacy section signs in a server command."""
    InterFace.say(
        RTextList(RText("[MirrorServerReforged] ", RColor.aqua), RText(message, color))
    )


def CopyWorld(source_root, target_root, world):
    """Copy a world into place without deleting the last good mirror first."""
    source = os.path.abspath(os.path.join(source_root, world))
    target = os.path.abspath(os.path.join(target_root, world))
    if not os.path.isdir(source):
        raise FileNotFoundError("源世界目录不存在: {}".format(source))
    try:
        common_path = os.path.commonpath((source, target))
    except ValueError:
        # Different Windows drives cannot overlap.
        common_path = None
    if source == target or common_path in (source, target):
        raise ValueError("镜像目录与源世界目录不能互相包含")

    target_parent = os.path.dirname(target)
    os.makedirs(target_parent, exist_ok=True)
    temporary_root = tempfile.mkdtemp(
        prefix=".{}-msr-sync-".format(os.path.basename(target)), dir=target_parent
    )
    staged = os.path.join(temporary_root, "new")
    previous = os.path.join(temporary_root, "previous")
    moved_previous = False
    try:
        shutil.copytree(source, staged, ignore=shutil.ignore_patterns("session.lock"))
        if os.path.exists(target):
            os.replace(target, previous)
            moved_previous = True
        try:
            os.replace(staged, target)
        except Exception:
            if moved_previous and not os.path.exists(target):
                os.replace(previous, target)
            raise
    finally:
        shutil.rmtree(temporary_root, ignore_errors=True)


@new_thread("MSR-Sync")
def ServerSync(InterFace):
    global syncFlag
    start_time = datetime.datetime.now()
    try:
        InterFace.execute("save-off")
        InterFace.execute("save-all")
        for world in config["world"]:
            CopyWorld(config["source"], config["target"], world)
        end_time = datetime.datetime.now()
        Broadcast(InterFace, "同步完成！用时{}".format(end_time - start_time))
    except Exception as e:
        InterFace.logger.exception("[MirrorServerReforged] 同步失败")
        Broadcast(InterFace, "同步失败！原因：{}".format(e), RColor.red)
    finally:
        InterFace.execute("save-on")
        syncFlag = False


def Sync():
    global syncFlag
    InterFace = GetInterFace()
    if syncFlag:
        Broadcast(
            InterFace,
            "服务器正在进行同步，请不要重复提交同步任务！",
            RColor.light_purple,
        )
    else:
        syncFlag = True
        Broadcast(InterFace, "正在同步服务器地图……")
        ServerSync(InterFace)


def EncodeVarInt(value):
    """Encode an integer as a Minecraft protocol VarInt."""
    data = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            data.append(byte | 0x80)
        else:
            data.append(byte)
            return bytes(data)


def DecodeVarInt(sock):
    """Read a Minecraft protocol VarInt from a socket."""
    value = 0
    shift = 0
    while True:
        chunk = sock.recv(1)
        if not chunk:
            raise OSError("连接已被对端关闭")
        byte = chunk[0]
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value
        shift += 7
        if shift > 35:
            raise OSError("VarInt 过长")


# The protocol version sent in the handshake packet of a Server List Ping.
# The mirror server answers the status request regardless of the version that is
# sent, as long as it is a valid VarInt, so the exact value does not matter here
MINECRAFT_PROTOCOL_VERSION = 767


def IsMinecraftServerReady(host, port, timeout=2):
    """Check if the Minecraft server on the given port finished starting up.

    A plain TCP connection is not enough: a Minecraft server binds its game port
    early during startup, so the port already accepts connections while the world
    is still loading. The server only answers a Server List Ping once it is ready
    for players, which is what makes the ping a reliable readiness signal.
    """
    try:
        with socket.create_connection((host, port), timeout) as connection:
            connection.settimeout(timeout)
            host_bytes = host.encode("utf-8")
            handshake = (
                b"\x00"
                + EncodeVarInt(MINECRAFT_PROTOCOL_VERSION)
                + EncodeVarInt(len(host_bytes))
                + host_bytes
                + struct.pack(">H", port)
                + b"\x01"
            )
            connection.sendall(EncodeVarInt(len(handshake)) + handshake)
            connection.sendall(EncodeVarInt(1) + b"\x00")
            DecodeVarInt(connection)  # packet length
            if DecodeVarInt(connection) != 0:  # packet id
                return False
            length = DecodeVarInt(connection)
            if length <= 0:
                return False
            payload = b""
            while len(payload) < length:
                chunk = connection.recv(length - len(payload))
                if not chunk:
                    return False
                payload += chunk
            json.loads(payload.decode("utf-8"))
            return True
    except Exception:
        return False


def ToAbsolutePath(target):
    """Resolve a config path against the directory MCDR was started in."""
    return target if os.path.isabs(target) else os.path.join(path, target)


def FindMirrorServerProperties():
    """Locate the server.properties of the mirror server, or None if it is missing.

    ``target`` is the parent directory of the mirrored worlds, which is also the
    mirror server's working directory when the mirror server runs under MCDR. In
    the legacy layout the server files live directly inside ./Mirror instead, so
    both locations are checked.
    """
    for root in (config.get("target"), "./Mirror"):
        if not root:
            continue
        candidate = os.path.normpath(
            os.path.join(ToAbsolutePath(root), "server.properties")
        )
        if os.path.isfile(candidate):
            return candidate
    return None


def ReadMirrorServerPort():
    """Read server-port from the mirror server's server.properties.

    Returns None when the port cannot be determined. The port is deliberately not
    guessed: the main server usually listens on 25565, so probing a guessed port
    could mistake the main server for the mirror server.
    """
    properties_path = FindMirrorServerProperties()
    if properties_path is None:
        return None
    try:
        with open(properties_path, "r", encoding="utf-8", errors="replace") as file:
            for line in file:
                line = line.strip()
                if line.startswith("server-port="):
                    return int(line.split("=", 1)[1].strip())
    except Exception:
        return None
    return None


def GetMirrorReadyProbes():
    """Build the probes used to detect that the mirror server is up.

    Both probes below only succeed once the mirror server really finished starting
    up, unlike a plain TCP connection to its game port.
    """
    probes = []
    port = ReadMirrorServerPort()
    if port is not None:
        probes.append(lambda: IsMinecraftServerReady("127.0.0.1", port))
    if config["rcon"]["enable"]:
        rcon_host = config["rcon"]["host"]
        rcon_port = config["rcon"]["port"]
        probes.append(lambda: IsRconPortOpen(rcon_host, rcon_port))
    return probes


def WaitForMirrorStart(process, timeout=None):
    """Wait until the mirror server finished starting up.

    :param process: The mirror server process, or None if it is unknown
    :param timeout: The longest time to wait, in second
    :return: One of the MIRROR_START_* constants
    """
    if timeout is None:
        timeout = START_WAIT_TIMEOUT
    probes = GetMirrorReadyProbes()
    if len(probes) == 0:
        return MIRROR_START_UNKNOWN
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process is not None and process.poll() is not None:
            return MIRROR_START_EXITED
        for probe in probes:
            if probe():
                return MIRROR_START_READY
        time.sleep(START_WAIT_INTERVAL)
    return MIRROR_START_TIMEOUT


def NotifyMirrorStart(InterFace, process):
    """Wait for the mirror server to come up, then tell the players about the result."""
    result = WaitForMirrorStart(process)
    if result == MIRROR_START_READY:
        Broadcast(InterFace, "镜像服已启动完成，现在可以转服了！")
    elif result == MIRROR_START_EXITED:
        Broadcast(
            InterFace,
            "镜像服启动失败！进程已退出，请查看镜像服控制台的报错信息。",
            RColor.red,
        )
    elif result == MIRROR_START_TIMEOUT:
        Broadcast(
            InterFace,
            "已启动镜像服，但等待{}秒后仍未检测到启动完成，请手动检查！".format(
                START_WAIT_TIMEOUT
            ),
            RColor.red,
        )
    else:
        Broadcast(
            InterFace,
            "无法确认镜像服是否启动完成：未找到镜像服的server.properties，且Rcon未开启！",
            RColor.red,
        )


@new_thread("MSR-Start")
def CommandExecute(InterFace):
    try:
        global MirrorProcess
        MirrorProcess = None
        if platform == "win32":
            MirrorProcess = subprocess.Popen(
                config["command"], creationflags=subprocess.CREATE_NEW_CONSOLE
            )
        else:
            MirrorProcess = subprocess.Popen(config["command"], shell=True)
    except Exception as e:
        Broadcast(InterFace, "启动失败！原因为：{}".format(e), RColor.red)
    os.chdir(path)


@new_thread("MSR-Main")
def ServerStart(InterFace):
    # global Started
    try:
        os.chdir("Mirror")
        CommandExecute(InterFace)
        time.sleep(5)
        os.chdir(path)
    except Exception as e:
        Broadcast(InterFace, "启动失败！原因为：{}".format(e), RColor.red)
        return
    NotifyMirrorStart(InterFace, GetMirrorProcess())


def Start(server):
    # global Started
    InterFace = GetInterFace()
    # if Started:
    #     server.reply('§b[MirrorServerReforged] §6镜像服正在运行……')
    # else:
    if syncFlag:
        Broadcast(
            InterFace,
            "镜像服正在进行同步，请在同步完成后再启动镜像服！",
            RColor.light_purple,
        )
    else:
        Broadcast(InterFace, "正在启动镜像服，这可能需要一定的时间……")
        Broadcast(InterFace, "启动完成后，请自行利用BungeeCord的转服或者直连进行转服！")
        # Started = True
        ServerStart(InterFace)


def GetInterFace(*args):
    global Interface
    InterFace = ServerInterface.get_instance().as_plugin_server_interface()
    return InterFace


# def Status(server):
#     if Started:
#         server.reply('§b[MirrorServerReforged] §6镜像服正在运行……')
#     else:
#         server.reply('§b[MirrorServerReforged] §6镜像服未运行……')


def IsRconPortOpen(host, port, timeout=1):
    """Check if the RCON port of the mirror server still accepts connections."""
    try:
        with socket.create_connection((host, port), timeout):
            return True
    except OSError:
        return False


def GetMirrorProcess():
    """Return the raw mirror server process handle, which may already have exited."""
    return globals().get("MirrorProcess")


def GetRunningMirrorProcess():
    """Return the mirror server process handle, or None if it is unknown or already exited."""
    process = GetMirrorProcess()
    if process is not None and process.poll() is None:
        return process
    return None


def WaitForMirrorStop(host, port, process, timeout=None):
    """Wait until the mirror server is fully stopped.

    The mirror server process is the most reliable signal, so it is used first.
    When the process handle is unavailable (for example the mirror server was started
    outside of this plugin), the RCON port is used instead: the mirror server is
    considered stopped once its RCON port no longer accepts connections.
    """
    if timeout is None:
        timeout = STOP_WAIT_TIMEOUT
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process is not None:
            if process.poll() is not None:
                return True
        elif not IsRconPortOpen(host, port):
            return True
        time.sleep(STOP_WAIT_INTERVAL)
    return False


@new_thread("MSR-Stop")
def StopMirrorServer(server):
    """Stop the mirror server via RCON and report the result back to the command source."""
    rcon_config = config["rcon"]
    host, port = rcon_config["host"], rcon_config["port"]
    # Only trust the process handle if it is still alive before the stop command is sent
    process = GetRunningMirrorProcess()
    conn = RconInit(host, port, rcon_config["password"])
    try:
        connected = conn.connect()
    except Exception as e:
        server.reply("§b[MirrorServerReforged] §6无法连接镜像服的Rcon！原因为：{}".format(e))
        return
    if not connected:
        server.reply(
            "§b[MirrorServerReforged] §6无法连接镜像服的Rcon，请检查配置文件中的Rcon信息！"
        )
        return
    try:
        conn.send_command("stop", max_retry_time=3)
    except Exception as e:
        server.reply("§b[MirrorServerReforged] §6无法停止镜像服！原因为：{}".format(e))
        return
    finally:
        try:
            conn.disconnect()
        except Exception:
            pass

    # Report the timeout value that was really used
    timeout = STOP_WAIT_TIMEOUT
    if WaitForMirrorStop(host, port, process, timeout):
        server.reply("§b[MirrorServerReforged] §6镜像服已彻底关闭！")
    else:
        server.reply(
            "§b[MirrorServerReforged] §6已向镜像服发送关闭指令，但等待{}秒后仍未检测到镜像服关闭，请手动检查！".format(
                timeout
            )
        )


def Stop(server):
    # global Started
    # if Started:
    if config["rcon"]["enable"]:
        server.reply("§b[MirrorServerReforged] §6正在向镜像服发送关闭指令……")
        StopMirrorServer(server)
    else:
        server.reply(
            "§b[MirrorServerReforged] §6无法通过Rcon停止镜像服，因为Rcon未开启！"
        )
    # else:
    #     server.reply('§b[MirrorServerReforged] §6镜像服未运行！')


def Reload(server):
    server.reply("§b[MirrorServerReforged] §6正在重载配置文件……")
    ConfigToDo()
    server.reply("§b[MirrorServerReforged] §6重载完成！")


def DisplayHelp(server):
    for line in help_msg.splitlines():
        server.reply(line)


@new_thread("MSR-Init")
def MCDRInitalize(server):
    if MCDR:
        try:
            os.chdir("Mirror")
            system = sys.platform
            if system == "win32":
                # Windows NT Platform
                os.system("python -m mcdreforged init")
            else:
                # Linux/Unix Platform
                os.system("python3 -m mcdreforged init")
            server.reply("§b[MirrorServerReforged] §6初始化已完成！")
        except Exception as e:
            server.reply("§b[MirrorServerReforged] §6初始化失败！原因为：{}".format(e))
        os.chdir(path)
    else:
        server.reply("§b[MirrorServerReforged] §6非MCDR类服务器，无需初始化！")


def Initalize(server):
    server.reply("§b[MirrorServerReforged] §6已启动初始化进程！")
    MCDRInitalize(server)


def ConfigToDo():
    if os.path.exists("./config/MirrorServerReforged.json"):
        LoadConfig()
    else:
        CreateConfig()


def on_load(server, prev):
    ConfigToDo()  # Load Config
    InitalizeOnFirstRun()  # Initalize if this is the first run
    # builder = SimpleCommandBuilder()
    server.register_help_message("!!msr", "MirrorServerReforged 帮助")
    server.register_command(
        Literal("!!msr")
        .runs(DisplayHelp)
        .then(Literal("help").runs(DisplayHelp))
        .then(Literal("sync").runs(Sync))
        .then(Literal("reload").runs(Reload))
        .then(Literal("start").runs(Start))
        .then(Literal("stop").runs(Stop))
        .then(Literal("init").runs(Initalize))
        # .then(Literal('status').runs(Status))
    )
    # register stop confirm command (TOO LAZY TO REBUILD THE PREVIOUS COMMAND)
    # builder.command('!!msr help', DisplayHelp)
    # builder.command('!!msr sync', Sync)
    # builder.command('!!msr reload', Reload)
    # builder.command('!!msr start', Start)
    # builder.command('!!msr stop', Stop)
    # builder.command('!!msr init', Initalize)
    # builder.register(server)
