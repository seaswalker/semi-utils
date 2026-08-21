import logging
import os
import re
import struct
from datetime import datetime
from enum import Enum
from pathlib import Path

from PIL import Image
from PIL.Image import Transpose
from dateutil import parser

from entity.config import ElementConfig
from enums.constant import *
from utils import calculate_pixel_count
from utils import extract_attribute
from utils import extract_gps_info
from utils import extract_gps_lat_and_long
from utils import get_exif

# HEIC 支持（可选依赖）：仅当安装 pillow-heif 时注册，否则 jpg 流程不受影响
try:
    import pillow_heif
    pillow_heif.register_heif_opener()
    _HEIF_AVAILABLE = True
except ImportError:
    _HEIF_AVAILABLE = False

logger = logging.getLogger(__name__)


class ExifId(Enum):
    CAMERA_MODEL = 'CameraModelName'
    CAMERA_MAKE = 'Make'
    LENS_MODEL = ['LensModel', 'Lens', 'LensID']
    LENS_MAKE = 'LensMake'
    DATETIME = 'DateTimeOriginal'
    FOCAL_LENGTH = 'FocalLength'
    FOCAL_LENGTH_IN_35MM_FILM = 'FocalLengthIn35mmFormat'
    F_NUMBER = 'FNumber'
    ISO = 'ISO'
    EXPOSURE_TIME = 'ExposureTime'
    SHUTTER_SPEED_VALUE = 'ShutterSpeedValue'
    ORIENTATION = 'Orientation'


PATTERN = re.compile(r"(\d+)\.")  # 匹配小数


def get_datetime(exif) -> datetime:
    dt = datetime.now()
    try:
        dt = parser.parse(extract_attribute(exif, ExifId.DATETIME.value,
                                            default_value=str(datetime.now())))
    except ValueError as e:
        logger.info(f'Error: 时间格式错误：{extract_attribute(exif, ExifId.DATETIME.value)}')
    return dt


def get_focal_length(exif):
    focal_length = DEFAULT_VALUE
    focal_length_in_35mm_film = DEFAULT_VALUE

    try:
        focal_lengths = PATTERN.findall(extract_attribute(exif, ExifId.FOCAL_LENGTH.value))
        try:
            focal_length = focal_lengths[0] if focal_length else DEFAULT_VALUE
        except IndexError as e:
            logger.info(
                f'ValueError: 不存在焦距：{focal_lengths} : {e}')
        try:
            focal_length_in_35mm_film: str = focal_lengths[1] if focal_length else DEFAULT_VALUE
        except IndexError as e:
            logger.info(f'ValueError: 不存在 35mm 焦距：{focal_lengths} : {e}')
    except Exception as e:
        logger.info(f'KeyError: 焦距转换错误：{extract_attribute(exif, ExifId.FOCAL_LENGTH.value)} : {e}')

    return focal_length, focal_length_in_35mm_film


class ImageContainer(object):
    def __init__(self, path: Path, is_use_equivalent_focal_length: bool):
        self.path: Path = path
        self.target_path: Path | None = None
        self.img: Image.Image = Image.open(path)
        # JPEG 源图的量化表，输出时严格复用源质量（transpose 会丢失该属性，需在转向前捕获）
        self.source_quantization = getattr(self.img, 'quantization', None)
        self.exif: dict = get_exif(path)
        # 图像信息
        self.original_width = self.img.width
        self.original_height = self.img.height

        self._param_dict = dict()

        self.model: str = extract_attribute(self.exif, ExifId.CAMERA_MODEL.value)
        self.make: str = extract_attribute(self.exif, ExifId.CAMERA_MAKE.value)
        self.lens_model: str = extract_attribute(self.exif, *ExifId.LENS_MODEL.value)
        self.lens_make: str = extract_attribute(self.exif, ExifId.LENS_MAKE.value)
        self.date: datetime = get_datetime(self.exif)
        self.focal_length, self.focal_length_in_35mm_film = get_focal_length(self.exif)
        self.f_number: str = extract_attribute(self.exif, ExifId.F_NUMBER.value, default_value=DEFAULT_VALUE)
        self.exposure_time: str = extract_attribute(self.exif, ExifId.EXPOSURE_TIME.value, default_value=DEFAULT_VALUE,
                                                    suffix='s')
        self.iso: str = extract_attribute(self.exif, ExifId.ISO.value, default_value=DEFAULT_VALUE)

        # 是否使用等效焦距
        self.use_equivalent_focal_length: bool = is_use_equivalent_focal_length

        # 修正图像方向
        self.orientation = self.exif[ExifId.ORIENTATION.value] if ExifId.ORIENTATION.value in self.exif else 1
        if self.orientation == "Rotate 0":
            pass
        elif self.orientation == "Rotate 90 CW":
            self.img = self.img.transpose(Transpose.ROTATE_270)
        elif self.orientation == "Rotate 180":
            self.img = self.img.transpose(Transpose.ROTATE_180)
        elif self.orientation == "Rotate 270 CW":
            self.img = self.img.transpose(Transpose.ROTATE_90)
        else:
            pass

        # 水印设置
        self.custom = '无'
        self.logo = None

        # 水印图片
        self.watermark_img = None

        self._param_dict[MODEL_VALUE] = self.model
        self._param_dict[PARAM_VALUE] = self.get_param_str()
        self._param_dict[MAKE_VALUE] = self.make
        self._param_dict[DATETIME_VALUE] = self._parse_datetime()
        self._param_dict[DATE_VALUE] = self._parse_date()
        self._param_dict[LENS_VALUE] = self.lens_model
        filename_without_ext = os.path.splitext(self.path.name)[0]
        self._param_dict[FILENAME_VALUE] = filename_without_ext
        self._param_dict[TOTAL_PIXEL_VALUE] = calculate_pixel_count(self.original_width, self.original_height)

        # GPS 信息
        if 'GPSPosition' in self.exif:
            self._param_dict[GEO_INFO_VALUE] = str.join(' ', extract_gps_info(self.exif.get('GPSPosition')))
        elif 'GPSLatitude' in self.exif and 'GPSLongitude' in self.exif:
            self._param_dict[GEO_INFO_VALUE] = str.join(' ', extract_gps_lat_and_long((self.exif.get('GPSLatitude'),
                                                                                       self.exif.get('GPSLongitude'))))
        else:
            self._param_dict[GEO_INFO_VALUE] = '无'

        self._param_dict[CAMERA_MAKE_CAMERA_MODEL_VALUE] = ' '.join(
            [self._param_dict[MAKE_VALUE], self._param_dict[MODEL_VALUE]])
        self._param_dict[LENS_MAKE_LENS_MODEL_VALUE] = ' '.join(
            [self.lens_make, self._param_dict[LENS_VALUE]])
        self._param_dict[CAMERA_MODEL_LENS_MODEL_VALUE] = ' '.join(
            [self._param_dict[MODEL_VALUE], self._param_dict[LENS_VALUE]])
        self._param_dict[DATE_FILENAME_VALUE] = ' '.join(
            [self._param_dict[DATE_VALUE], self._param_dict[FILENAME_VALUE]])
        self._param_dict[DATETIME_FILENAME_VALUE] = ' '.join(
            [self._param_dict[DATETIME_VALUE], self._param_dict[FILENAME_VALUE]])

    def get_height(self):
        return self.get_watermark_img().height

    def get_width(self):
        return self.get_watermark_img().width

    def get_model(self):
        return self.model

    def get_make(self):
        return self.make

    def get_ratio(self):
        return self.img.width / self.img.height

    def get_img(self):
        return self.img

    def _parse_datetime(self) -> str:
        """
        解析日期，转换为指定的格式
        :return: 指定格式的日期字符串，转换失败返回原始的时间字符串
        """
        return datetime.strftime(self.date, '%Y-%m-%d %H:%M')

    def _parse_date(self) -> str:
        """
        解析日期，转换为指定的格式
        :return: 指定格式的日期字符串，转换失败返回原始的时间字符串
        """
        return datetime.strftime(self.date, '%Y-%m-%d')

    def get_attribute_str(self, element: ElementConfig) -> str:
        """
        通过 element 获取属性值
        :param element: element 对象有 name 和 value 两个字段，通过 name 和 value 获取属性值
        :return: 属性值字符串
        """
        if element.get_name() in self._param_dict:
            return self._param_dict[element.get_name()]

        if element is None or element.get_name() == '':
            return ''
        if element.get_name() == CUSTOM_VALUE:
            self.custom = element.get_value()
            return self.custom
        elif element.get_name() in self._param_dict:
            return self._param_dict[element.get_name()]
        else:
            return ''

    def get_param_str(self) -> str:
        """
        组合拍摄参数，输出一个字符串
        :return: 拍摄参数字符串
        """
        focal_length = self.focal_length_in_35mm_film if self.use_equivalent_focal_length else self.focal_length
        return '  '.join([str(focal_length) + 'mm', 'f/' + self.f_number, self.exposure_time,
                          'ISO' + str(self.iso)])

    def get_original_height(self):
        return self.original_height

    def get_original_width(self):
        return self.original_width

    def get_original_ratio(self):
        return self.original_width / self.original_height

    def get_logo(self):
        return self.logo

    def set_logo(self, logo) -> None:
        self.logo = logo

    def is_use_equivalent_focal_length(self, flag: bool) -> None:
        self.use_equivalent_focal_length = flag

    def get_watermark_img(self) -> Image.Image:
        if self.watermark_img is None:
            self.watermark_img = self.img.copy()
        return self.watermark_img

    def update_watermark_img(self, watermark_img) -> None:
        if self.watermark_img == watermark_img:
            return
        original_watermark_img = self.watermark_img
        self.watermark_img = watermark_img
        if original_watermark_img is not None:
            original_watermark_img.close()

    def close(self):
        self.img.close()
        self.watermark_img.close()

    def _get_source_subsampling(self):
        """
        从源 JPEG 的 SOF marker 解析色度抽样格式，保证输出与源保持一致。
        非 JPEG 源或解析失败返回 None，由 Pillow 按 quality 默认处理。
        """
        try:
            with open(self.path, 'rb') as f:
                data = f.read(2048)
        except OSError:
            return None
        i = 2
        while i + 12 < len(data):
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                          0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                n_components = data[i + 9]
                if n_components < 3:
                    return None
                h, v = data[i + 11] >> 4, data[i + 11] & 0xF
                if h == 2 and v == 2:
                    return '4:2:0'
                if h == 2 and v == 1:
                    return '4:2:2'
                if h == 1 and v == 1:
                    return '4:4:4'
                return None
            length = struct.unpack('>H', data[i + 2:i + 4])[0]
            if length < 2:
                return None
            i += 2 + length
        return None

    def save(self, target_path, quality=100):
        if self.orientation == "Rotate 0":
            pass
        elif self.orientation == "Rotate 90 CW":
            self.watermark_img = self.watermark_img.transpose(Transpose.ROTATE_90)
        elif self.orientation == "Rotate 180":
            self.watermark_img = self.watermark_img.transpose(Transpose.ROTATE_180)
        elif self.orientation == "Rotate 270 CW":
            self.watermark_img = self.watermark_img.transpose(Transpose.ROTATE_270)
        else:
            pass

        if self.watermark_img.mode != 'RGB':
            self.watermark_img = self.watermark_img.convert('RGB')

        save_kwargs = dict(encoding='utf-8')
        if 'exif' in self.img.info:
            save_kwargs['exif'] = self.img.info['exif']

        output_format = str(target_path.suffix).lower().lstrip('.')
        if output_format == 'heic':
            if not _HEIF_AVAILABLE:
                raise RuntimeError(
                    '输出 HEIC 需要 pillow-heif，请先安装：pip install pillow-heif（并确保 libheif 已安装）')
            # HEIC 输出：质量语义与 JPEG 不同，直接用配置质量档位（65 视觉约等于 JPEG 90），
            # 体积约为 JPEG 同视觉质量的一半
            save_kwargs.update(format='HEIF', quality=quality, optimize=True)
        else:
            # JPEG 输出策略：优先复用源 JPEG 的量化表，保证原图区域质量与源文件严格一致；
            # 无法复用（非 JPEG 源）时回退到配置的输出质量
            save_kwargs.update(quality=50 if self.source_quantization else quality,
                               optimize=True)
            if self.source_quantization:
                # quality=50 使 Pillow 对 qtables 的缩放系数为 100%，量化表原样生效，
                # 从而让原图区域与源文件使用完全相同的量化步长；optimize 进一步压缩体积
                save_kwargs['qtables'] = self.source_quantization
                subsampling = self._get_source_subsampling()
                if subsampling:
                    save_kwargs['subsampling'] = subsampling

        self.watermark_img.save(target_path, **save_kwargs)
