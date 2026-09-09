"""Media source for Ajax Security photos."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from homeassistant.components.media_player import MediaClass  # type: ignore[attr-defined]
from homeassistant.components.media_source import (
    BrowseMediaSource,
    MediaSource,
    MediaSourceItem,
    PlayMedia,
    Unresolvable,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

from custom_components.aegis_ajax.const import DOMAIN
from custom_components.aegis_ajax.photo_storage import PHOTOS_BASE_DIR


async def async_get_media_source(hass: HomeAssistant) -> AjaxPhotoMediaSource:
    """Set up Ajax photo media source."""
    return AjaxPhotoMediaSource(hass)


class AjaxPhotoMediaSource(MediaSource):
    """Provide Ajax Security photos as browsable media."""

    name = "Ajax Security Photos"

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize source."""
        super().__init__(DOMAIN)
        self.hass = hass

    @property
    def _base_path(self) -> Path:
        """Return base path for Ajax photos."""
        media_dir = self.hass.config.media_dirs.get("local", "/media")
        return Path(media_dir) / PHOTOS_BASE_DIR

    async def async_resolve_media(self, item: MediaSourceItem) -> PlayMedia:
        """Resolve a photo to a playable URL."""
        if not item.identifier:
            raise Unresolvable("No identifier provided")

        file_path = self._base_path / item.identifier
        try:
            file_path.resolve().relative_to(self._base_path.resolve())
        except ValueError as err:
            raise Unresolvable("Invalid path") from err

        if not file_path.is_file():
            raise Unresolvable(f"File not found: {item.identifier}")

        return PlayMedia(
            url=f"/media/local/{PHOTOS_BASE_DIR}/{item.identifier}",
            mime_type="image/jpeg",
        )

    async def async_browse_media(self, item: MediaSourceItem) -> BrowseMediaSource:
        """Browse photo folders and files."""
        if not item.identifier:
            return await self._browse_root()
        return await self._browse_folder(item.identifier)

    async def _browse_root(self) -> BrowseMediaSource:
        """List device folders."""
        base = self._base_path

        def _scan_root() -> list[BrowseMediaSource]:
            items: list[BrowseMediaSource] = []
            if base.is_dir():
                for folder in sorted(base.iterdir()):
                    if folder.is_dir():
                        photo_count = sum(
                            1
                            for f in folder.rglob("*.jpg")
                            if f.name not in {"last.jpg", "preview.jpg"}
                        )
                        items.append(
                            BrowseMediaSource(
                                domain=DOMAIN,
                                identifier=folder.name,
                                media_class=MediaClass.DIRECTORY,
                                media_content_type="",
                                title=f"{folder.name} ({photo_count})",
                                can_play=False,
                                can_expand=True,
                            )
                        )
            return items

        children = await self.hass.async_add_executor_job(_scan_root)

        return BrowseMediaSource(
            domain=DOMAIN,
            identifier=None,
            media_class=MediaClass.APP,
            media_content_type="",
            title="Ajax Security Photos",
            can_play=False,
            can_expand=True,
            children=children,
        )

    async def _browse_folder(self, identifier: str) -> BrowseMediaSource:
        """List albums and individual photos below a device folder."""
        folder_path = self._base_path / identifier
        try:
            folder_path.resolve().relative_to(self._base_path.resolve())
        except ValueError:
            return await self._browse_root()

        def _title_for_name(name: str, *, is_album: bool = False) -> str:
            # Alarm albums use `YYYY-MM-DD_HH-MM-SS-microseconds`; older
            # individual captures retain their legacy timestamp filename.
            if len(name) >= 19 and name[4:5] == "-" and name[10:11] == "_":
                timestamp = name[:10] + " " + name[11:19].replace("-", ":")
                return f"Alarm {timestamp}" if is_album else timestamp
            return name

        def _scan_folder() -> list[BrowseMediaSource]:
            items: list[BrowseMediaSource] = []
            if folder_path.is_dir():
                for album in sorted(
                    (entry for entry in folder_path.iterdir() if entry.is_dir()),
                    key=lambda entry: entry.name,
                    reverse=True,
                ):
                    relative = album.relative_to(self._base_path).as_posix()
                    preview = album / "preview.jpg"
                    items.append(
                        BrowseMediaSource(
                            domain=DOMAIN,
                            identifier=relative,
                            media_class=MediaClass.DIRECTORY,
                            media_content_type="",
                            title=_title_for_name(album.name, is_album=True),
                            can_play=False,
                            can_expand=True,
                            thumbnail=(
                                f"/media/local/{PHOTOS_BASE_DIR}/{relative}/preview.jpg"
                                if preview.is_file()
                                else None
                            ),
                        )
                    )
                photos = sorted(
                    [
                        f
                        for f in folder_path.iterdir()
                        if f.is_file()
                        and f.suffix == ".jpg"
                        and f.name not in {"last.jpg", "preview.jpg"}
                    ],
                    key=lambda f: f.name,
                    # Device-level captures are newest first; frame numbers in
                    # an alarm album must retain their chronological sequence.
                    reverse=folder_path.parent == self._base_path,
                )
                for photo in photos:
                    relative = photo.relative_to(self._base_path).as_posix()
                    items.append(
                        BrowseMediaSource(
                            domain=DOMAIN,
                            identifier=relative,
                            media_class=MediaClass.IMAGE,
                            media_content_type="image/jpeg",
                            title=_title_for_name(photo.stem),
                            can_play=True,
                            can_expand=False,
                            thumbnail=f"/media/local/{PHOTOS_BASE_DIR}/{relative}",
                        )
                    )
            return items

        children = await self.hass.async_add_executor_job(_scan_folder)

        return BrowseMediaSource(
            domain=DOMAIN,
            identifier=identifier,
            media_class=MediaClass.DIRECTORY,
            media_content_type="",
            title=_title_for_name(folder_path.name, is_album=folder_path.parent != self._base_path),
            can_play=False,
            can_expand=True,
            children=children,
            children_media_class=MediaClass.IMAGE,
        )
