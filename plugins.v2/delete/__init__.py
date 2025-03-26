import datetime
import threading
from typing import List, Tuple, Dict, Any, Optional

import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from app.core.config import settings
from app.helper.downloader import DownloaderHelper
from app.log import logger
from app.plugins import _PluginBase
from app.schemas import ServiceInfo


class Delete(_PluginBase):
    # 插件名称
    plugin_name = "自动删除"
    # 插件描述
    plugin_desc = "删除qb、tr中tracker长期无法访问的下载任务"
    # 插件图标
    plugin_icon = "Youtube-dl_C.png"
    # 插件版本
    plugin_version = "1.0.2"
    # 插件作者
    plugin_author = "ClarkChen"
    # 作者主页
    author_url = "https://github.com/aClarkChen"
    # 插件配置项ID前缀
    plugin_config_prefix = "Delete_"
    # 加载顺序
    plugin_order = 23
    # 可使用的用户级别
    auth_level = 2
    # 日志前缀
    LOG_TAG = "[Delete]"
    

    # 退出事件
    _event = threading.Event()
    # 私有属性
    sites_helper = None
    downloader_helper = None
    _scheduler = None
    _enabled = False
    _onlyonce = False
    _delete = False
    _times= 7
    _interval = "计划任务"
    _interval_cron = "0 14 * * *"
    _interval_time = 24
    _interval_unit = "小时"
    _downloaders = None
    _tag_map = "忽略标签"
    _delete_config = ""

    def init_plugin(self, config: dict = None):
        self.downloader_helper = DownloaderHelper()
        # 读取配置
        if config:
            self._enabled = config.get("enabled")
            self._onlyonce = config.get("onlyonce")
            self._delete = config.get("delete")
            self._times = self.str_to_number(config.get("times"), 7)
            self._interval = config.get("interval") or "计划任务"
            self._interval_cron = config.get("interval_cron") or "0 14 * * *"
            self._interval_time = self.str_to_number(config.get("interval_time"), 24)
            self._interval_unit = config.get("interval_unit") or "小时"
            self._downloaders = config.get("downloaders")
            self._tag_map = config.get("tag_map") or "忽略标签"
            self._delete_config = config.get("delete_config")

        # 停止现有任务
        self.stop_service()

        if self._onlyonce:
            # 创建定时任务控制器
            self._scheduler = BackgroundScheduler(timezone=settings.TZ)
            # 执行一次, 关闭onlyonce
            self._onlyonce = False
            config.update({"onlyonce": self._onlyonce})
            self.update_config(config)
            self._scheduler.add_job(func=self._complete_delete, trigger='date',
                                    run_date=datetime.datetime.now(tz=pytz.timezone(settings.TZ)) + datetime.timedelta(seconds=3))
            if self._scheduler and self._scheduler.get_jobs():
                # 启动服务
                self._scheduler.print_jobs()
                self._scheduler.start()

    @property
    def service_infos(self) -> Optional[Dict[str, ServiceInfo]]:
        if not self._downloaders:
            logger.warning("尚未配置下载器，请检查配置")
            return None

        services = self.downloader_helper.get_services(name_filters=self._downloaders)
        if not services:
            logger.warning("获取下载器实例失败，请检查配置")
            return None

        active_services = {}
        for service_name, service_info in services.items():
            if service_info.instance.is_inactive():
                logger.warning(f"下载器 {service_name} 未连接，请检查配置")
            else:
                active_services[service_name] = service_info

        if not active_services:
            logger.warning("没有已连接的下载器，请检查配置")
            return None

        return active_services

    def get_state(self) -> bool:
        return self._enabled

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        pass

    def get_api(self) -> List[Dict[str, Any]]:
        pass

    def get_service(self) -> List[Dict[str, Any]]:
        """
        注册插件公共服务
        [{
            "id": "服务ID",
            "name": "服务名称",
            "trigger": "触发器：cron/interval/date/CronTrigger.from_crontab()",
            "func": self.xxx,
            "kwargs": {} # 定时器参数
        }]
        """
        if self._enabled:
            if self._interval == "计划任务" or self._interval == "固定间隔":
                if self._interval == "固定间隔":
                    if self._interval_unit == "小时":
                        return [{
                            "id": "Delete",
                            "name": "自动删除",
                            "trigger": "interval",
                            "func": self._complete_delete,
                            "kwargs": {
                                "hours": self._interval_time
                            }
                        }]
                    else:
                        if self._interval_time < 5:
                            self._interval_time = 5
                            logger.info(f"{self.LOG_TAG}启动定时服务: 最小不少于5分钟, 防止执行间隔太短任务冲突")
                        return [{
                            "id": "Delete",
                            "name": "自动删除",
                            "trigger": "interval",
                            "func": self._complete_delete,
                            "kwargs": {
                                "minutes": self._interval_time
                            }
                        }]
                else:
                    return [{
                        "id": "Delete",
                        "name": "自动删除",
                        "trigger": CronTrigger.from_crontab(self._interval_cron),
                        "func": self._complete_delete,
                        "kwargs": {}
                    }]
        return []

    def _complete_delete(self):
        if not self.service_infos:
            return
        self._old_config = {}
        self._new_config = ''
        if self._enabled and self._delete_config:
            for item in self._delete_config.split("\n"):
                i = item.split(":")
                _hash = i[2]
                _times = int(i[1])
                self._old_config[_hash] = _times
        tag_map = []
        if self._tag_map:
            tag_map = self._tag_map.split("\n")
        logger.info(f"{self.LOG_TAG}开始执行 ...")
        for service in self.service_infos.values():
            downloader = service.name
            downloader_obj = service.instance
            logger.info(f"{self.LOG_TAG}开始扫描下载器 {downloader} ...")
            if not downloader_obj:
                logger.error(f"{self.LOG_TAG} 获取下载器失败 {downloader}")
                continue
            # 获取下载器中的种子
            torrents, error = downloader_obj.get_torrents()
            # 如果下载器获取种子发生错误 或 没有种子 则跳过
            if error or not torrents:
                continue
            logger.info(f"{self.LOG_TAG}下载器 {downloader} 分析种子信息中 ...")
            for torrent in torrents:
                try:
                    if self._event.is_set():
                        logger.info(f"{self.LOG_TAG}停止服务")
                        return
                    # 获取种子当前标签
                    torrent_tags = self._get_tags(torrent=torrent, dl_type=service.type)
                    for tag in torrent_tags:
                        if tag in tag_map:
                            break
                    else:
                        self._check(service=service, torrent=torrent)
                except Exception as e:
                    logger.error(f"{self.LOG_TAG}分析种子信息时发生了错误: {str(e)}")
        self.update_config({"delete_config": self._new_config})
        logger.info(f"{self.LOG_TAG}执行完成")

    def _check(self, service: ServiceInfo, torrent):
        if not service or not service.instance:
            return
        _hash = self._get_hash(torrent=torrent, dl_type=service.type)
        downloader_obj = service.instance
        # 下载器api不通用, 因此需分开处理
        if service.type == "qbittorrent":
            trackers = downloader_obj.qbc.torrents_trackers(_hash)
            for tracker in trackers:
                if tracker.status == 2:
                    break
            else:
                self._handle(service=service, torrent=torrent, hash=_hash)
        else:
            stats = torrent.tracker_stats
            for stat in stats:
                if stat.last_announce_result == 'Success':
                    break
            else:
                self._handle(service=service, torrent=torrent, hash=_hash)

    def _handle(self, service: ServiceInfo, torrent, hash):
        downloader_obj = service.instance
        name = self._get_name(torrent=torrent, dl_type=service.type)
        if hash in self._old_config:
            time = self._old_config[hash] + 1
            if time > self._times:
                try:
                    downloader_obj.delete_torrents(ids=hash, delete_file=False)
                    logger.warn(f"{self.LOG_TAG}下载器: {service.name} 种子: {name} 已失联{time}次, 已删除")
                except ValueError:
                    logger.warn(f"{self.LOG_TAG}下载器: {service.name} 种子删除失败")
            else:
                self._new_config += f'{name}:{time}:{hash}\n'
                logger.warn(f"{self.LOG_TAG}下载器: {service.name} 种子: {name} 已失联{time}次, 持续记录中")
        else:
            self._new_config += f'{name}:{1}:{hash}\n'
            logger.warn(f"{self.LOG_TAG}下载器: {service.name} 种子: {name} 已记录")

    @staticmethod
    def str_to_number(s: str, i: int) -> int:
        try:
            return int(s)
        except ValueError:
            return i

    @staticmethod
    def _get_hash(torrent: Any, dl_type: str):
        try:
            return torrent.get("hash") if dl_type == "qbittorrent" else torrent.hashString
        except Exception as e:
            print(str(e))
            return ""

    @staticmethod
    def _get_tags(torrent: Any, dl_type: str):
        try:
            return [str(tag).strip() for tag in torrent.get("tags", "").split(',')] if dl_type == "qbittorrent" else torrent.labels or []
        except Exception as e:
            print(str(e))
            return []

    @staticmethod
    def _get_name(torrent: Any, dl_type: str):
        try:
            return torrent.get("name") if dl_type == "qbittorrent" else torrent.name
        except Exception as e:
            print(str(e))
            return ""

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        return ([
            {
                'component': 'VForm',
                'content': [
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 3
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'enabled',
                                            'label': '启用插件',
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 3
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'delete',
                                            'label': '自动删除',
                                        },
                                    }
                                ],
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 3
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'dialog_closed',
                                            'label': '查看记录',
                                        },
                                    }
                                ],
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 3
                                },
                                'content': [
                                    {
                                        'component': 'VCheckboxBtn',
                                        'props': {
                                            'model': 'onlyonce',
                                            'label': '运行一次'
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                "component": "VCol",
                                "props": {
                                    "cols": 12
                                },
                                "content": [
                                    {
                                        "component": "VTextarea",
                                        "props": {
                                            "model": "times",
                                            "label": "失效次数",
                                            "placeholder": "7",
                                        },
                                    }
                                ],
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12
                                },
                                'content': [
                                    {
                                        'component': 'VSelect',
                                        'props': {
                                            'multiple': True,
                                            'chips': True,
                                            'clearable': True,
                                            'model': 'downloaders',
                                            'label': '下载器',
                                            'items': [{"title": config.name, "value": config.name}
                                                      for config in self.downloader_helper.get_configs().values()]
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 6,
                                    'md': 3
                                },
                                'content': [
                                    {
                                        'component': 'VSelect',
                                        'props': {
                                            'model': 'interval',
                                            'label': '定时任务',
                                            'items': [
                                                {'title': '禁用', 'value': '禁用'},
                                                {'title': '计划任务', 'value': '计划任务'},
                                                {'title': '固定间隔', 'value': '固定间隔'}
                                            ]
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 6,
                                    'md': 3,
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'interval_cron',
                                            'label': '计划任务设置',
                                            'placeholder': '0 14 * * *'
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 6,
                                    'md': 3,
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'interval_time',
                                            'label': '时间间隔, 每',
                                            'placeholder': '24'
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 6,
                                    'md': 3,
                                },
                                'content': [
                                    {
                                        'component': 'VSelect',
                                        'props': {
                                            'model': 'interval_unit',
                                            'label': '单位',
                                            'items': [
                                                {'title': '小时', 'value': '小时'},
                                                {'title': '分钟', 'value': '分钟'}
                                            ]
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {
                                    "cols": 12
                                },
                                "content": [
                                    {
                                        "component": "VTextarea",
                                        "props": {
                                            "model": "tag_map",
                                            "label": "标签",
                                            "rows": 5,
                                            "placeholder": "如:XX\nYY",
                                        },
                                    }
                                ],
                            }
                        ],
                    },
                    {
                        "component": "VDialog",
                        "props": {
                            "model": "dialog_closed",
                            "max-width": "60rem",
                            "overlay-class": "v-dialog--scrollable v-overlay--scroll-blocked",
                            "content-class": "v-card v-card--density-default v-card--variant-elevated rounded-t"
                        },
                        "content": [
                            {
                                "component": "VCard",
                                "props": {
                                    "title": "记录"
                                },
                                "content": [
                                    {
                                        "component": "VDialogCloseBtn",
                                        "props": {
                                            "model": "dialog_closed"
                                        }
                                    },
                                    {
                                        "component": "VCardText",
                                        "props": {
                                            'cols': 12,
                                        },
                                        "content": [
                                            {
                                                'component': 'VAceEditor',
                                                'props': {
                                                    'model': 'delete_config',
                                                    'style': 'height: 30rem'
                                                }
                                            }
                                        ]
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }
        ],
        {
            "enabled": False,
            "onlyonce": False,
            "delete": False,
            "times": "7",
            "interval": "计划任务",
            "interval_cron": "0 14 * * *",
            "interval_time": "24",
            "interval_unit": "小时",
            "tag_map": "忽略标签",
            "delete_config": ""
        })

    def get_page(self) -> List[dict]:
        pass

    def stop_service(self):
        try:
            if self._scheduler:
                self._scheduler.remove_all_jobs()
                if self._scheduler.running:
                    self._event.set()
                    self._scheduler.shutdown()
                    self._event.clear()
                self._scheduler = None
        except Exception as e:
            print(str(e))
