import datetime
import threading
from typing import List, Tuple, Dict, Any, Optional

import pytz
from app.helper.sites import SitesHelper
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from app.core.config import settings
from app.helper.downloader import DownloaderHelper
from app.log import logger
from app.plugins import _PluginBase
from app.schemas import ServiceInfo
from app.utils.string import StringUtils


class Tag(_PluginBase):
    # 插件名称
    plugin_name = "自动标签"
    # 插件描述
    plugin_desc = "给qb、tr的下载任务贴标签(支持自定义)"
    # 插件图标
    plugin_icon = "Youtube-dl_B.png"
    # 插件版本
    plugin_version = "1.2.1"
    # 插件作者
    plugin_author = "ClarkChen"
    # 作者主页
    author_url = "https://github.com/aClarkChen"
    # 插件配置项ID前缀
    plugin_config_prefix = "Tag_"
    # 加载顺序
    plugin_order = 21
    # 可使用的用户级别
    auth_level = 2
    # 日志前缀
    LOG_TAG = "[Tag]"

    # 退出事件
    _event = threading.Event()
    # 私有属性
    sites_helper = None
    downloader_helper = None
    _scheduler = None
    _enabled = False
    _onlyonce = False
    _cover = False
    _site_first = False
    _interval = "计划任务"
    _interval_cron = "0 12 * * *"
    _interval_time = 24
    _interval_unit = "小时"
    _downloaders = None
    _tracker_map = "tracker地址:站点标签"
    _save_path_map = "保存地址:标签"

    def init_plugin(self, config: dict = None):
        self.sites_helper = SitesHelper()
        self.downloader_helper = DownloaderHelper()
        # 读取配置
        if config:
            self._enabled = config.get("enabled")
            self._onlyonce = config.get("onlyonce")
            self._cover = config.get("cover")
            self._site_first = config.get("site_first")
            self._interval = config.get("interval") or "计划任务"
            self._interval_cron = config.get("interval_cron") or "0 12 * * *"
            self._interval_time = self.str_to_number(config.get("interval_time"), 24)
            self._interval_unit = config.get("interval_unit") or "小时"
            self._downloaders = config.get("downloaders")
            self._tracker_map = config.get("tracker_map") or "tracker地址:站点标签"
            self._save_path_map = config.get("save_path_map") or "保存地址:标签"

        # 停止现有任务
        self.stop_service()

        if self._onlyonce:
            # 创建定时任务控制器
            self._scheduler = BackgroundScheduler(timezone=settings.TZ)
            # 执行一次, 关闭onlyonce
            self._onlyonce = False
            config.update({"onlyonce": self._onlyonce})
            self.update_config(config)
            # 启动自动标签
            self._scheduler.add_job(func=self._complemented_tags, trigger='date',
                                    run_date=datetime.datetime.now(
                                        tz=pytz.timezone(settings.TZ)) + datetime.timedelta(seconds=3)
                                    )
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
                            "id": "Tag",
                            "name": "自动补全标签",
                            "trigger": "interval",
                            "func": self._complemented_tags,
                            "kwargs": {
                                "hours": self._interval_time
                            }
                        }]
                    else:
                        if self._interval_time < 5:
                            self._interval_time = 5
                            logger.info(f"{self.LOG_TAG}启动定时服务: 最小不少于5分钟, 防止执行间隔太短任务冲突")
                        return [{
                            "id": "Tag",
                            "name": "自动补全标签",
                            "trigger": "interval",
                            "func": self._complemented_tags,
                            "kwargs": {
                                "minutes": self._interval_time
                            }
                        }]
                else:
                    return [{
                        "id": "Tag",
                        "name": "自动补全标签",
                        "trigger": CronTrigger.from_crontab(self._interval_cron),
                        "func": self._complemented_tags,
                        "kwargs": {}
                    }]
        return []

    @staticmethod
    def str_to_number(s: str, i: int) -> int:
        try:
            return int(s)
        except ValueError:
            return i

    def _complemented_tags(self):
        if not self.service_infos:
            return
        logger.info(f"{self.LOG_TAG}开始执行 ...")
        # 所有站点索引
        indexers = [indexer.get("name") for indexer in self.sites_helper.get_indexers()]
        indexers = set(indexers)
        tracker_maps = self._tracker_map.split("\n")
        save_path_maps = self._save_path_map.split("\n")
        tracker_map = {}
        save_path_map = {}
        for item in tracker_maps:
            i = item.split(":")
            _tracker = i[0]
            _label = i[1]
            tracker_map[_tracker] = _label
        for item in save_path_maps:
            i = item.split(":")
            _path = i[0]
            _label = i[1]
            save_path_map[_path] = _label
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
                    # 获取种子hash
                    _hash = self._get_hash(torrent=torrent, dl_type=service.type)
                    # 获取种子存储地址
                    _path = self._get_path(torrent=torrent, dl_type=service.type)
                    if not _hash or not _path:
                        continue
                    torrent_labels = []
                    for key, label in save_path_map.items():
                        if key in _path:
                            torrent_labels.append(label)
                            break
                    site = None
                    torrent_tags = None
                    if service.type == "qbittorrent":
                        torrent_tags = self._get_tags(torrent=torrent, dl_type=service.type)
                        if self._cover:
                            downloader_obj.qbc.torrents_remove_tags(torrent_hashes= _hash,tags= torrent_tags)
                            torrent_tags = None
                        else:
                            site = indexers.intersection(set(torrent_tags))
                    else:
                        if not self._cover:
                            torrent_tags = self._get_tags(torrent=torrent, dl_type=service.type)
                            site = indexers.intersection(set(torrent_tags))
                    if not site:
                        trackers = self._get_trackers(torrent=torrent, dl_type=service.type)
                        for tracker in trackers:
                            for key, label in tracker_map.items():
                                if key in tracker:
                                    site = label
                                    break
                            else:
                                domain = StringUtils.get_url_domain(tracker)
                                site_info = self.sites_helper.get_indexer(domain)
                                if site_info:
                                    site = site_info.get("name")
                            if site:
                                torrent_labels.append(site)
                                break
                    if torrent_labels:
                        self._set_torrent_info(service=service, _hash=_hash, _tags=torrent_labels, _original_tags=torrent_tags)
                except Exception as e:
                    logger.error(
                        f"{self.LOG_TAG}分析种子信息时发生了错误: {str(e)}")
        logger.info(f"{self.LOG_TAG}执行完成")

    @staticmethod
    def _get_hash(torrent: Any, dl_type: str):
        try:
            return torrent.get("hash") if dl_type == "qbittorrent" else torrent.hashString
        except Exception as e:
            print(str(e))
            return ""

    @staticmethod
    def _get_path(torrent: Any, dl_type: str):
        try:
            return torrent.get("save_path") if dl_type == "qbittorrent" else torrent.download_dir
        except Exception as e:
            print(str(e))
            return ""

    @staticmethod
    def _get_trackers(torrent: Any, dl_type: str):
        try:
            if dl_type == "qbittorrent":
                return [tracker.get("url") for tracker in (torrent.trackers or []) if tracker.get("tier", -1) >= 0 and tracker.get("url")]
            else:
                return [tracker.announce for tracker in (torrent.trackers or []) if tracker.tier >= 0 and tracker.announce]
        except Exception as e:
            print(str(e))
            return []

    @staticmethod
    def _get_tags(torrent: Any, dl_type: str):
        try:
            return [str(tag).strip() for tag in torrent.get("tags", "").split(',')] \
                if dl_type == "qbittorrent" else torrent.labels or []
        except Exception as e:
            print(str(e))
            return []

    def _set_torrent_info(self, service: ServiceInfo, _hash: str, _tags=None, _original_tags: list = None):
        if not service or not service.instance:
            return
        downloader_obj = service.instance
        # 下载器api不通用, 因此需分开处理
        if _original_tags:
            if service.type == "qbittorrent":
                _tags = list(set(_tags) - set(_original_tags))
                downloader_obj.set_torrents_tag(ids=_hash, tags=_tags)
            else:
                downloader_obj.set_torrent_tag(ids=_hash, tags=_tags, org_tags=_original_tags)
        else:
            if service.type == "qbittorrent":
                downloader_obj.set_torrents_tag(ids=_hash, tags=_tags)
            else:
                if self._site_first:
                    _tags = _tags[::-1]
                downloader_obj.trc.change_torrent(ids=_hash,labels=_tags)
        logger.warn(f"{self.LOG_TAG}下载器: {service.name} 种子id: {_hash} {('  标签: ' + ','.join(_tags)) if _tags else ''}")

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        return [
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
                                            'model': 'cover',
                                            'label': '覆盖模式',
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
                                            'model': 'site_first',
                                            'label': '站点优先',
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
                                        'component': 'VCheckboxBtn',
                                        'props': {
                                            'model': 'onlyonce',
                                            'label': '运行一次'
                                        }
                                    }
                                ]
                            },
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
                                            'placeholder': '0 12 * * *'
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
                                            "model": "tracker_map",
                                            "label": "tracker网址:站点标签",
                                            "rows": 5,
                                            "placeholder": "如:tracker.XXX:XX",
                                        },
                                    }
                                ],
                            }
                        ],
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
                                            "model": "save_path_map",
                                            "label": "保存地址:标签",
                                            "rows": 5,
                                            "placeholder": "如:/volume1/XX保种/:XX保种\n/volume1/保种/:保种",
                                        },
                                    }
                                ],
                            }
                        ],
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
                                        'component': 'VAlert',
                                        'props': {
                                            'type': 'info',
                                            'variant': 'tonal',
                                            'text': '每行配置一个，只会匹配一个，行数越高优先级越高。注意！！需用英文的:。'
                                        }
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }
        ], {
            "enabled": False,
            "onlyonce": False,
            "cover": False,
            "site_first": False,
            "interval": "计划任务",
            "interval_cron": "0 12 * * *",
            "interval_time": "24",
            "interval_unit": "小时",
            "tracker_map": "tracker地址:站点标签",
            "save_path_map": "保存地址:标签"
        }

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
