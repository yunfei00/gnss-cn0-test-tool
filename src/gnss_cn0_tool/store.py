import json
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from uuid import UUID, uuid4


@dataclass(frozen=True)
class Scene:
    scene_id: str
    mode: str = "auto"
    stabilize_seconds: float = 20
    measure_seconds: float = 60
    repeat: int = 1
    manual_timeout_seconds: float = 180
    instructions: str = ""
    particles: int = 250
    cpu_threads: int = 2
    on_failure: str = "abort"

    def __post_init__(self):
        if not self.scene_id or self.mode not in {"auto", "manual"}:
            raise ValueError("场景 ID 或模式无效")
        if not 0 <= self.stabilize_seconds <= 3600 or not 0 < self.measure_seconds <= 3600:
            raise ValueError("稳定等待或采样时长超出范围")
        if type(self.repeat) is not int or not 1 <= self.repeat <= 100:
            raise ValueError("重复次数必须在 1 到 100 之间")
        if not 0 < self.manual_timeout_seconds <= 3600 or self.on_failure not in {"abort", "skip"}:
            raise ValueError("人工确认超时或失败策略无效")
        if not 20 <= self.particles <= 1000 or not 1 <= self.cpu_threads <= 8:
            raise ValueError("游戏参数超出范围")


@dataclass(frozen=True)
class Plan:
    scenes: tuple[Scene, ...]
    max_uncertainty_ms: float = 500
    frequency_tolerance_hz: int = 2_000_000
    min_samples: int = 1
    collect_nmea: bool = False
    collect_raw: bool = False

    def __post_init__(self):
        if not self.scenes or not 0 < self.max_uncertainty_ms <= 10000:
            raise ValueError("计划至少需要一个场景和有效时钟不确定度阈值")
        if not 0 < self.frequency_tolerance_hz <= 10_000_000 or self.min_samples < 1:
            raise ValueError("频率容差或样本阈值无效")
        if type(self.collect_nmea) is not bool or type(self.collect_raw) is not bool:
            raise ValueError("采集选项必须为布尔值")

    @classmethod
    def load(cls, path: Path):
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if data.pop("schema_version") != 1:
            raise ValueError("不支持的计划版本")
        return cls(tuple(Scene(**scene) for scene in data.pop("scenes")), **data)

    def save(self, path: Path):
        write_json(path, {"schema_version": 1, **asdict(self)})

    @classmethod
    def default(cls):
        return cls((
            Scene("baseline"), Scene("video"), Scene("baseline"), Scene("game_heavy"),
            Scene("baseline"),
            Scene("navigation_manual", "manual", instructions="请在手机上打开导航并启动路线"),
            Scene("baseline"),
        ))


def write_json(path: Path, value, *, exclusive: bool = False):
    if exclusive:
        with path.open("x", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        return
    temporary = path.with_suffix(path.suffix + ".partial")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8"
    )
    temporary.replace(path)


class RunStore:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS runs(
                    id TEXT PRIMARY KEY, state TEXT NOT NULL, created_ms INTEGER NOT NULL,
                    reason TEXT
                );
                CREATE TABLE IF NOT EXISTS transitions(
                    id INTEGER PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id),
                    scene_id TEXT, state TEXT NOT NULL, reason TEXT, utc_ns INTEGER NOT NULL,
                    monotonic_ns INTEGER NOT NULL
                );
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.root / "history.sqlite")
        try:
            with db:
                db.execute("PRAGMA foreign_keys=ON")
                yield db
        finally:
            db.close()

    def create(self, plan: Plan) -> str:
        run_id = str(uuid4())
        folder = self.path(run_id)
        folder.mkdir()
        plan.save(folder / "test_plan.json")
        with self.connect() as db:
            db.execute("INSERT INTO runs VALUES (?, 'CREATED', ?, NULL)",
                       (run_id, time.time_ns() // 1_000_000))
        return run_id

    def path(self, run_id: str) -> Path:
        if str(UUID(run_id)) != run_id:
            raise ValueError("运行 ID 格式无效")
        return self.root / run_id

    def transition(self, run_id: str, state: str, scene_id=None, reason=None):
        with self.connect() as db:
            db.execute("UPDATE runs SET state=?, reason=? WHERE id=?", (state, reason, run_id))
            db.execute(
                "INSERT INTO transitions(run_id,scene_id,state,reason,utc_ns,monotonic_ns)"
                " VALUES (?,?,?,?,?,?)",
                (run_id, scene_id, state, reason, time.time_ns(), time.monotonic_ns()),
            )

    def history(self):
        with self.connect() as db:
            return db.execute("SELECT id,state,reason FROM runs ORDER BY created_ms DESC").fetchall()
