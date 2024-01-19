# Copyright 2021-2024 the openage authors. See copying.md for legal info.
#
# pylint: disable=too-many-arguments,too-many-locals
"""
Converts media requested by export requests to files.
"""
from __future__ import annotations
import typing

import logging
import os
import multiprocessing

from openage.convert.entity_object.export.texture import Texture
from openage.convert.service import debug_info
from openage.convert.service.export.load_media_cache import load_media_cache
from openage.convert.value_object.read.media.blendomatic import Blendomatic
from openage.convert.value_object.read.media_types import MediaType
from openage.log import dbg, info, get_loglevel
from openage.util.strings import format_progress

if typing.TYPE_CHECKING:
    from argparse import Namespace

    from openage.convert.entity_object.export.media_export_request import MediaExportRequest
    from openage.convert.value_object.read.media.colortable import ColorTable
    from openage.convert.value_object.init.game_version import GameVersion
    from openage.util.fslike.path import Path


class MediaExporter:
    """
    Provides functions for converting media files and writing them to a targetdir.
    """

    @staticmethod
    def export(
        export_requests: dict[MediaType, list[MediaExportRequest]],
        sourcedir: Path,
        exportdir: Path,
        args: Namespace
    ) -> None:
        """
        Converts files requested by MediaExportRequests.

        :param export_requests: Export requests for media files. This is a dict of export requests
                                by their media type.
        :param sourcedir: Directory where all media assets are mounted. Source subfolder and
                          source filename should be stored in the export request.
        :param exportdir: Directory the resulting file(s) will be exported to. Target subfolder
                          and target filename should be stored in the export request.
        :param args: Converter arguments.
        :type export_requests: dict
        :type sourcedir: Path
        :type exportdir: Path
        :type args: Namespace
        """
        cache_info = {}
        if args.game_version.edition.media_cache:
            cache_info = load_media_cache(args.game_version.edition.media_cache)

        for media_type in export_requests.keys():
            cur_export_requests = export_requests[media_type]

            kwargs = {}
            if media_type is MediaType.TERRAIN:
                info("-- Exporting terrain files...")
                MediaExporter._export_terrains(
                    cur_export_requests,
                    sourcedir,
                    exportdir,
                    args.palettes,
                    args.game_version,
                    args.compression_level
                )

            elif media_type is MediaType.GRAPHICS:
                info("-- Exporting graphics files...")
                MediaExporter._export_graphics(
                    cur_export_requests,
                    sourcedir,
                    exportdir,
                    args.palettes,
                    args.compression_level,
                    cache_info
                )

            elif media_type is MediaType.SOUNDS:
                kwargs["debugdir"] = args.debugdir
                kwargs["loglevel"] = args.debug_info
                info("-- Exporting sound files...")
                MediaExporter._export_sound(
                    cur_export_requests,
                    sourcedir,
                    exportdir,
                    **kwargs
                )

            elif media_type is MediaType.BLEND:
                info("-- Exporting blend files...")
                MediaExporter._export_blend(
                    cur_export_requests,
                    sourcedir,
                    exportdir,
                    args.blend_mode_count
                )

        if args.debug_info > 5:
            cachedata = {}
            for request in export_requests[MediaType.GRAPHICS]:
                kwargs = {}
                kwargs["palettes"] = args.palettes
                kwargs["compression_level"] = args.compression_level

                cache = MediaExporter._get_media_cache(
                    request,
                    sourcedir,
                    args.palettes,
                    compression_level=2
                )

                cachedata[request] = cache

            debug_info.debug_media_cache(
                args.debugdir,
                args.debug_info,
                sourcedir,
                cachedata,
                args.game_version
            )

    @staticmethod
    def _export_blend(
        requests: list[MediaExportRequest],
        sourcedir: Path,
        exportdir: Path,
        blend_mode_count: int = None
    ) -> None:
        """
        Convert and export a blending mode.

        :param requests: Export requests for blending masks.
        :param sourcedir: Directory where all media assets are mounted. Source subfolder and
                          source filename should be stored in the export request.
        :param exportdir: Directory the resulting file(s) will be exported to. Target subfolder
                          and target filename should be stored in the export request.
        :param blend_mode_count: Number of blending modes extracted from the source file.
        :type requests: list[MediaExportRequest]
        :type sourcedir: Path
        :type exportdir: Path
        :type blend_mode_count: int
        """
        # Create a manager for sharing data between the workers and main process
        with multiprocessing.Manager() as manager:
            # Create a queue for data sharing
            # it's not actually used for passing data, only for counting
            # finished tasks
            outqueue = manager.Queue()

            # Create a pool of workers
            with multiprocessing.Pool() as pool:
                for request in requests:
                    # Feed the worker with the source file data (bytes) from the
                    # main process
                    #
                    # This is necessary because some image files are inside an
                    # archive and cannot be accessed asynchronously
                    source_file = sourcedir[request.get_type().value,
                                            request.source_filename]

                    # The target path must be native
                    target_path = exportdir[request.targetdir,
                                            request.target_filename].resolve_native_path()

                    # Start an export call in a worker process
                    # The call is asynchronous, so the next worker can be
                    # started immediately
                    pool.apply_async(
                        _export_blend,
                        args=(
                            source_file.open("rb").read(),
                            outqueue,
                            target_path,
                            blend_mode_count
                        )
                    )

                    # Log file information
                    if get_loglevel() <= logging.DEBUG:
                        MediaExporter.log_fileinfo(
                            source_file,
                            exportdir[request.targetdir, request.target_filename]
                        )

                    # Show progress
                    print(f"-- Files done: {format_progress(outqueue.qsize(), len(requests))}",
                          end = "\r", flush = True)

                # Close the pool since all workers have been started
                pool.close()

                # Show progress for remaining workers
                while outqueue.qsize() < len(requests):
                    print(f"-- Files done: {format_progress(outqueue.qsize(), len(requests))}",
                          end = "\r", flush = True)

                # Wait for all workers to finish
                pool.join()

    @staticmethod
    def _export_graphics(
        requests: list[MediaExportRequest],
        sourcedir: Path,
        exportdir: Path,
        palettes: dict[int, ColorTable],
        compression_level: int,
        cache_info: dict = None
    ) -> None:
        """
        Convert and export graphics file requests (multi-threaded).

        :param requests: Export requests for graphics files.
        :param sourcedir: Directory where all media assets are mounted. Source subfolder and
                          source filename should be stored in the export request.
        :param exportdir: Directory the resulting file(s) will be exported to. Target subfolder
                          and target filename should be stored in the export request.
        :param palettes: Palettes used by the game.
        :param compression_level: PNG compression level for the resulting image file.
        :param cache_info: Media cache information with compression parameters from a previous run.
        :type requests: list[MediaExportRequest]
        :type sourcedir: Path
        :type exportdir: Path
        :type palettes: dict
        :type compression_level: int
        :type cache_info: tuple
        """
        # Create a manager for sharing data between the workers and main process
        with multiprocessing.Manager() as manager:
            # Workers write the image metadata to this queue
            # so that it can be forwarded to the export requests
            #
            # we cannot do this in a worker process directly
            # because the export requests cannot be pickled
            outqueue = manager.Queue()

            # Create a pool of workers
            with multiprocessing.Pool() as pool:
                for idx, request in enumerate(requests):
                    # Feed the worker with the source file data (bytes) from the
                    # main process
                    #
                    # This is necessary because some image files are inside an
                    # archive and cannot be accessed asynchronously
                    source_file = sourcedir[request.get_type().value,
                                            request.source_filename]
                    if not source_file.exists():
                        if source_file.suffix.lower() in (".smx", ".sld"):
                            # Some DE2 graphics files have the wrong extension
                            # Fall back to the SMP (beta) extension
                            other_filename = request.source_filename[:-3] + "smp"
                            source_file = sourcedir[
                                request.get_type().value,
                                other_filename
                            ]
                            request.set_source_filename(other_filename)

                    # The target path must be native
                    target_path = exportdir[request.targetdir,
                                            request.target_filename].resolve_native_path()

                    # Start an export call in a worker process
                    # The call is asynchronous, so the next worker can be
                    # started immediately
                    pool.apply_async(
                        _export_texture,
                        args=(
                            idx,
                            source_file.open("rb").read(),
                            outqueue,
                            request.source_filename,
                            target_path,
                            palettes,
                            compression_level,
                            cache_info
                        )
                    )

                    # Log file information
                    if get_loglevel() <= logging.DEBUG:
                        MediaExporter.log_fileinfo(
                            source_file,
                            exportdir[request.targetdir, request.target_filename]
                        )

                    # Show progress
                    print(f"-- Files done: {format_progress(outqueue.qsize(), len(requests))}",
                          end = "\r", flush = True)

                # Close the pool since all workers have been started
                pool.close()

                # Show progress for remaining workers
                while outqueue.qsize() < len(requests):
                    print(f"-- Files done: {format_progress(outqueue.qsize(), len(requests))}",
                          end = "\r", flush = True)

                # Wait for all workers to finish
                pool.join()

            # Collect the metadata from the workers and forward it to the
            # export requests
            while not outqueue.empty():
                idx, metadata = outqueue.get()
                update_data = {requests[idx].target_filename: metadata}
                requests[idx].set_changed()
                requests[idx].notify_observers(update_data)
                requests[idx].clear_changed()

    @staticmethod
    def _export_interface(
        export_request: MediaExportRequest,
        sourcedir: Path,
        **kwargs
    ) -> None:
        """
        Convert and export a sprite file.
        """
        # TODO: Implement

    @staticmethod
    def _export_palette(
        export_request: MediaExportRequest,
        sourcedir: Path,
        **kwargs
    ) -> None:
        """
        Convert and export a palette file.
        """
        # TODO: Implement

    @staticmethod
    def _export_sound(
        requests: list[MediaExportRequest],
        sourcedir: Path,
        exportdir: Path,
        **kwargs
    ) -> None:
        """
        Convert and export sound files (multi-threaded).

        :param requests: Export requests for sound files.
        :param sourcedir: Directory where all media assets are mounted. Source subfolder and
                          source filename should be stored in the export request.
        :param exportdir: Directory the resulting file(s) will be exported to. Target subfolder
                          and target filename should be stored in the export request.
        :type requests: list[MediaExportRequest]
        :type sourcedir: Path
        :type exportdir: Path
        """
        # Create a manager for sharing data between the workers and main process
        with multiprocessing.Manager() as manager:
            # Create a queue for data sharing
            # it's not actually used for passing data, only for counting
            # finished tasks
            outqueue = manager.Queue()

            # Create a pool of workers
            with multiprocessing.Pool() as pool:
                sound_count = len(requests)
                for request in requests:
                    # Feed the worker with the source file data (bytes) from the
                    # main process
                    #
                    # This is necessary because some image files are inside an
                    # archive and cannot be accessed asynchronously
                    source_file = sourcedir[request.get_type().value,
                                            request.source_filename]

                    if source_file.is_file():
                        with source_file.open_r() as infile:
                            media_file = infile.read()

                    else:
                        # TODO: Filter files that do not exist out sooner
                        debug_info.debug_not_found_sounds(kwargs["debugdir"],
                                                          kwargs["loglevel"],
                                                          source_file)
                        sound_count -= 1
                        continue

                    # The target path must be native
                    target_path = exportdir[request.targetdir,
                                            request.target_filename].resolve_native_path()

                    # Start an export call in a worker process
                    # The call is asynchronous, so the next worker can be
                    # started immediately
                    pool.apply_async(
                        _export_sound,
                        args=(
                            media_file,
                            outqueue,
                            target_path
                        )
                    )

                    # Log file information
                    if get_loglevel() <= logging.DEBUG:
                        MediaExporter.log_fileinfo(
                            source_file,
                            exportdir[request.targetdir, request.target_filename]
                        )

                    # Show progress
                    print(f"-- Files done: {format_progress(outqueue.qsize(), sound_count)}",
                          end = "\r", flush = True)

                # Close the pool since all workers have been started
                pool.close()

                # Show progress for remaining workers
                while outqueue.qsize() < sound_count:
                    print(f"-- Files done: {format_progress(outqueue.qsize(), sound_count)}",
                          end = "\r", flush = True)

                # Wait for all workers to finish
                pool.join()

    @staticmethod
    def _export_terrains(
        requests: list[MediaExportRequest],
        sourcedir: Path,
        exportdir: Path,
        palettes: dict[int, ColorTable],
        game_version: GameVersion,
        compression_level: int
    ) -> None:
        """
        Convert and export terrain graphics files (multi-threaded).

        :param requests: Export requests for terrain graphics files.
        :param sourcedir: Directory where all media assets are mounted. Source subfolder and
                          source filename should be stored in the export request.
        :param exportdir: Directory the resulting file(s) will be exported to. Target subfolder
                          and target filename should be stored in the export request.
        :param game_version: Game edition and expansion info.
        :param palettes: Palettes used by the game.
        :param compression_level: PNG compression level for the resulting image file.
        :type requests: list[MediaExportRequest]
        :type sourcedir: Directory
        :type exportdir: Directory
        :type palettes: dict
        :type game_version: GameVersion
        :type compression_level: int
        """
        # Create a manager for sharing data between the workers and main process
        with multiprocessing.Manager() as manager:
            # Create a queue for data sharing
            # it's not actually used for passing data, only for counting
            # finished tasks
            outqueue = manager.Queue()

            # Create a pool of workers
            with multiprocessing.Pool() as pool:
                for request in requests:
                    # Feed the worker with the source file data (bytes) from the
                    # main process
                    #
                    # This is necessary because some image files are inside an
                    # archive and cannot be accessed asynchronously
                    source_file = sourcedir[request.get_type().value,
                                            request.source_filename]
                    if not source_file.exists():
                        if source_file.suffix.lower() in (".smx", ".sld"):
                            # Some DE2 graphics files have the wrong extension
                            # Fall back to the SMP (beta) extension
                            other_filename = request.source_filename[:-3] + "smp"
                            source_file = sourcedir[
                                request.get_type().value,
                                other_filename
                            ]
                            request.set_source_filename(other_filename)

                    # The target path must be native
                    target_path = exportdir[request.targetdir,
                                            request.target_filename].resolve_native_path()

                    # Start an export call in a worker process
                    # The call is asynchronous, so the next worker can be
                    # started immediately
                    pool.apply_async(
                        _export_terrain,
                        args=(
                            source_file.open("rb").read(),
                            outqueue,
                            request.source_filename,
                            target_path,
                            palettes,
                            compression_level,
                            game_version
                        )
                    )

                    # Log file information
                    if get_loglevel() <= logging.DEBUG:
                        MediaExporter.log_fileinfo(
                            source_file,
                            exportdir[request.targetdir, request.target_filename]
                        )

                    # Show progress
                    print(f"-- Files done: {format_progress(outqueue.qsize(), len(requests))}",
                          end = "\r", flush = True)

                # Close the pool since all workers have been started
                pool.close()

                # Show progress for remaining workers
                while outqueue.qsize() < len(requests):
                    print(f"-- Files done: {format_progress(outqueue.qsize(), len(requests))}",
                          end = "\r", flush = True)

                # Wait for all workers to finish
                pool.join()

    @staticmethod
    def _get_media_cache(
        export_request: MediaExportRequest,
        sourcedir: Path,
        palettes: dict[int, ColorTable],
        compression_level: int
    ) -> None:
        """
        Convert a media file and return the used settings. This performs
        a dry run, i.e. the graphics media is not saved on the filesystem.

        :param export_request: Export request for a graphics file.
        :param sourcedir: Directory where all media assets are mounted. Source subfolder and
                          source filename should be stored in the export request.
        :param exportdir: Directory the resulting file(s) will be exported to. Target subfolder
                          and target filename should be stored in the export request.
        :param palettes: Palettes used by the game.
        :param compression_level: PNG compression level for the resulting image file.
        :type export_request: MediaExportRequest
        :type sourcedir: Path
        :type exportdir: Path
        :type palettes: dict
        :type compression_level: int
        """
        source_file = sourcedir[
            export_request.get_type().value,
            export_request.source_filename
        ]

        try:
            media_file = source_file.open("rb")

        except FileNotFoundError:
            if source_file.suffix.lower() == ".smx":
                # Rename extension to SMP and try again
                other_filename = export_request.source_filename[:-1] + "p"
                source_file = sourcedir[
                    export_request.get_type().value,
                    other_filename
                ]

            media_file = source_file.open("rb")

        if source_file.suffix.lower() == ".slp":
            from ...value_object.read.media.slp import SLP
            image = SLP(media_file.read())

        elif source_file.suffix.lower() == ".smp":
            from ...value_object.read.media.smp import SMP
            image = SMP(media_file.read())

        elif source_file.suffix.lower() == ".smx":
            from ...value_object.read.media.smx import SMX
            image = SMX(media_file.read())

        elif source_file.suffix.lower() == ".sld":
            from ...value_object.read.media.sld import SLD
            image = SLD(media_file.read())

        else:
            raise SyntaxError(f"Source file {source_file.name} has an unrecognized extension: "
                              f"{source_file.suffix.lower()}")

        from .texture_merge import merge_frames
        texture = Texture(image, palettes)
        merge_frames(texture)
        MediaExporter.save_png(
            texture,
            None,
            None,
            compression_level=compression_level,
            cache=None,
            dry_run=True
        )

        return texture.get_cache_params()

    @staticmethod
    def save_png(
        texture: Texture,
        targetdir: Path,
        filename: str,
        compression_level: int = 1,
        cache: dict = None,
        dry_run: bool = False
    ) -> None:
        """
        Store the image data into the target directory path,
        with given filename="dir/out.png".

        :param texture: Texture with an image atlas.
        :param targetdir: Directory where the image file is created.
        :param filename: Name of the resulting image file.
        :param compression_level: PNG compression level used for the resulting image file.
        :param dry_run: If True, create the PNG but don't save it as a file.
        :type texture: Texture
        :type targetdir: Directory
        :type filename: str
        :type compression_level: int
        :type dry_run: bool
        """
        from ...service.export.png import png_create

        compression_levels = {
            0: png_create.CompressionMethod.COMPR_NONE,
            1: png_create.CompressionMethod.COMPR_DEFAULT,
            2: png_create.CompressionMethod.COMPR_OPTI,
            3: png_create.CompressionMethod.COMPR_GREEDY,
            4: png_create.CompressionMethod.COMPR_AGGRESSIVE,
        }

        if not dry_run:
            _, ext = os.path.splitext(filename)

            # only allow png
            if ext != ".png":
                raise ValueError("Filename invalid, a texture must be saved"
                                 f"as '*.png', not '*.{ext}'")

        compression_method = compression_levels.get(
            compression_level,
            png_create.CompressionMethod.COMPR_DEFAULT
        )
        png_data, compr_params = png_create.save(
            texture.image_data.data,
            compression_method,
            cache
        )

        if not dry_run:
            with targetdir[filename].open("wb") as imagefile:
                imagefile.write(png_data)

        if compr_params:
            texture.best_compr = (compression_level, *compr_params)

    @staticmethod
    def log_fileinfo(
        source_file: Path,
        target_file: Path
    ) -> None:
        """
        Log source and target file information to the shell.
        """
        source_format = source_file.suffix[1:].upper()
        target_format = target_file.suffix[1:].upper()

        source_path = source_file.resolve_native_path()
        if source_path:
            source_size = os.path.getsize(source_path)

        else:
            with source_file.open('r') as src:
                src.seek(0, os.SEEK_END)
                source_size = src.tell()

        target_path = target_file.resolve_native_path()
        target_size = os.path.getsize(target_path)

        log = ("Converted: "
               f"{source_file.name} "
               f"({source_format}, {source_size}B) "
               f"-> {target_file.name} "
               f"({target_format}, {target_size}B | "
               f"{(target_size / source_size * 100) - 100:+.1f}%)")

        dbg(log)


def _export_blend(
    blendfile_data: bytes,
    outqueue: multiprocessing.Queue,
    target_path: str,
    blend_mode_count: int = None
) -> None:
    """
    Convert and export a blending mode.

    :param blendfile_data: Raw file data of the blending mask.
    :param outqueue: Queue for passing metadata to the main process.
    :param target_path: Path to the resulting image file.
    :param blend_mode_count: Number of blending modes extracted from the source file.
    :type blendfile_data: bytes
    :type outqueue: multiprocessing.Queue
    :type target_path: str
    :type blend_mode_count: int
    """
    blend_data = Blendomatic(blendfile_data, blend_mode_count)

    from .texture_merge import merge_frames

    textures = blend_data.get_textures()
    for idx, texture in enumerate(textures):
        merge_frames(texture)
        _save_png(
            texture,
            f"{target_path}{idx}.png"
        )

    outqueue.put(0)


def _export_sound(
    sound_data: bytes,
    outqueue: multiprocessing.Queue,
    target_path: str
) -> None:
    """
    Convert and export a sound file.

    :param sound_data: Raw file data of the sound file.
    :param outqueue: Queue for passing metadata to the main process.
    :param target_path: Path to the resulting sound file.
    :type sound_data: bytes
    :type outqueue: multiprocessing.Queue
    :type target_path: str
    """
    from ...service.export.opus.opusenc import encode
    encoded = encode(sound_data)

    if isinstance(encoded, (str, int)):
        raise RuntimeError(f"opusenc failed: {encoded}")

    with open(target_path, "wb") as outfile:
        outfile.write(encoded)

    outqueue.put(0)


def _export_terrain(
    graphics_data: bytes,
    outqueue: multiprocessing.Queue,
    source_filename: str,
    target_path: str,
    palettes: dict[int, ColorTable],
    compression_level: int,
    game_version: GameVersion
) -> None:
    """
    Convert and export a terrain graphics file.

    :param graphics_data: Raw file data of the graphics file.
    :param outqueue: Queue for passing the image metadata to the main process.
    :param source_filename: Filename of the source file.
    :param target_path: Path to the resulting image file.
    :param palettes: Palettes used by the game.
    :param compression_level: PNG compression level for the resulting image file.
    :param game_version: Game edition and expansion info.
    :type graphics_data: bytes
    :type outqueue: multiprocessing.Queue
    :type source_filename: str
    :type target_path: str
    :type palettes: dict
    :type compression_level: int
    :type game_version: GameVersion
    """
    file_ext = source_filename.split('.')[-1].lower()
    if file_ext == "slp":
        from ...value_object.read.media.slp import SLP
        image = SLP(graphics_data)

    elif file_ext == "dds":
        # TODO: Implement
        pass

    elif file_ext == "png":
        with open(target_path, "wb") as imagefile:
            imagefile.write(graphics_data)

        return

    else:
        raise SyntaxError(f"Source file {source_filename} has an unrecognized extension: "
                          f"{file_ext}")

    if game_version.edition.game_id in ("AOC", "SWGB"):
        from .terrain_merge import merge_terrain
        texture = Texture(image, palettes)
        merge_terrain(texture)

    else:
        from .texture_merge import merge_frames
        texture = Texture(image, palettes)
        merge_frames(texture)

    _save_png(
        texture,
        target_path,
        compression_level=compression_level
    )

    outqueue.put(0)


def _export_texture(
    export_request_id: int,
    graphics_data: bytes,
    outqueue: multiprocessing.Queue,
    source_filename: str,
    target_path: str,
    palettes: dict[int, ColorTable],
    compression_level: int,
    cache_info: dict = None
) -> None:
    """
    Convert and export a graphics file to a PNG texture.

    :param export_request_id: ID of the export request.
    :param graphics_data: Raw file data of the graphics file.
    :param outqueue: Queue for passing the image metadata to the main process.
    :param source_filename: Filename of the source file.
    :param target_path: Path to the resulting image file.
    :param palettes: Palettes used by the game.
    :param compression_level: PNG compression level for the resulting image file.
    :param cache_info: Media cache information with compression parameters from a previous run.
    :type export_request_id: int
    :type graphics_data: bytes
    :type outqueue: multiprocessing.Queue
    :type source_filename: str
    :type target_path: str
    :type palettes: dict
    :type compression_level: int
    :type cache_info: tuple
    """
    file_ext = source_filename.split('.')[-1].lower()
    if file_ext == "slp":
        from ...value_object.read.media.slp import SLP
        image = SLP(graphics_data)

    elif file_ext == "smp":
        from ...value_object.read.media.smp import SMP
        image = SMP(graphics_data)

    elif file_ext == "smx":
        from ...value_object.read.media.smx import SMX
        image = SMX(graphics_data)

    elif file_ext == "sld":
        from ...value_object.read.media.sld import SLD
        image = SLD(graphics_data)

    else:
        raise SyntaxError(f"Source file {source_filename} has an unrecognized extension: "
                          f"{file_ext}")

    packer_cache = None
    compr_cache = None
    if cache_info:
        cache_params = cache_info.get(source_filename, None)

        if cache_params:
            packer_cache = cache_params["packer_settings"]
            compression_level = cache_params["compr_settings"][0]
            compr_cache = cache_params["compr_settings"][1:]

    from .texture_merge import merge_frames

    texture = Texture(image, palettes)
    merge_frames(texture, cache=packer_cache)
    _save_png(
        texture,
        target_path,
        compression_level=compression_level,
        cache=compr_cache
    )
    metadata = (export_request_id, texture.get_metadata().copy())
    outqueue.put(metadata)


def _save_png(
    texture: Texture,
    target_path: str,
    compression_level: int = 1,
    cache: dict = None,
    dry_run: bool = False
) -> None:
    """
    Store the image data into the target directory path,
    with given target_path="dir/out.png".

    :param texture: Texture with an image atlas.
    :param target_path: Path to the resulting image file.
    :param compression_level: PNG compression level used for the resulting image file.
    :param dry_run: If True, create the PNG but don't save it as a file.
    :type texture: Texture
    :type target_path: str
    :type compression_level: int
    :type dry_run: bool
    """
    from ...service.export.png import png_create

    compression_levels = {
        0: png_create.CompressionMethod.COMPR_NONE,
        1: png_create.CompressionMethod.COMPR_DEFAULT,
        2: png_create.CompressionMethod.COMPR_OPTI,
        3: png_create.CompressionMethod.COMPR_GREEDY,
        4: png_create.CompressionMethod.COMPR_AGGRESSIVE,
    }

    if not dry_run:
        _, ext = os.path.splitext(target_path)

        # only allow png
        if ext != b".png":
            raise ValueError("Filename invalid, a texture must be saved"
                             f" as '*.png', not '*.{ext}'")

    compression_method = compression_levels.get(
        compression_level,
        png_create.CompressionMethod.COMPR_DEFAULT
    )
    png_data, compr_params = png_create.save(
        texture.image_data.data,
        compression_method,
        cache
    )

    if not dry_run:
        with open(target_path, "wb") as imagefile:
            imagefile.write(png_data)

    if compr_params:
        texture.best_compr = (compression_level, *compr_params)
