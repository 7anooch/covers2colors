import colorsys
from urllib.error import HTTPError
from urllib.error import URLError
from urllib.request import urlopen
import json
from pathlib import Path
from typing import Optional, Union
import matplotlib.pyplot as plt
import matplotlib as mpl
from mpl_toolkits.axes_grid1 import make_axes_locatable
import numpy as np
from kneed import KneeLocator
from PIL import Image
from sklearn.cluster import KMeans
from matplotlib.colors import ListedColormap
from sklearn.cluster import MiniBatchKMeans
from .album_art import get_best_cover_art_url, load_api_keys
from .colorblind import is_colorblind_friendly
from scipy.spatial.distance import pdist, squareform

# Directory where palettes are stored
PALETTE_DIR = Path.home() / ".covers2colors" / "palettes"
INDEX_FILE = PALETTE_DIR / "index.json"

def _ensure_palette_dir() -> None:
    """Create the palette directory if it does not exist."""
    PALETTE_DIR.mkdir(parents=True, exist_ok=True)


def _load_index(assign_ids: bool = False) -> list:
    """Return the contents of ``index.json`` upgrading entries if needed.

    When ``assign_ids`` is ``True`` any palette entries missing an ``id``
    field are assigned a numeric identifier and the file is updated on disk.
    """

    _ensure_palette_dir()

    if INDEX_FILE.exists():
        try:
            with INDEX_FILE.open("r") as f:
                data = json.load(f)
        except json.JSONDecodeError:
            data = []
    else:
        data = []

    if assign_ids:
        next_id = max([entry.get("id", 0) for entry in data], default=0)
        updated = False
        for entry in data:
            if "id" not in entry:
                next_id += 1
                entry["id"] = next_id
                updated = True
        if updated:
            with INDEX_FILE.open("w") as f:
                json.dump(data, f, indent=2)

    return data

class CoverPalette:
    """
    A class to convert album artwork to a numpy array of RGB values.

    Args:
        artist (str): The name of the artist.
        album (str): The name of the album.

    Attributes:
        image_path (str): The URL of the cover art image.
        album (str): The name of the album.
        image (PIL.Image): The PIL Image object of the cover art.
        pixels (numpy.ndarray): A numpy array of RGB values representing the cover art.
        transparent_pixels (numpy.ndarray): A boolean numpy array where True indicates the corresponding pixel in the cover art is transparent.
        kmeans (KMeans): The KMeans object after fitting to the RGB values. None if the `fit_kmeans` method has not been called.
        hexcodes (list): The list of hexcodes representing the dominant colors in the cover art. None if the `get_hexcodes` method has not been called.
        is_colorblind_friendly (bool | None): Result of automatically checking
            the latest generated palette for color-blind friendliness.
    """

    def __init__(self, artist, album):
        """
        Initializes the CoverPalette object by fetching the cover art and converting it to a numpy array of RGB values.
        """
        api_key, discogs_token = load_api_keys()

        cover_art_url = get_best_cover_art_url(
            artist,
            album,
            api_key=api_key,
            user_token=discogs_token,
        )
        if not cover_art_url:
            raise ValueError(f"Cover art not found for {artist} - {album}")

        self.artist = artist
        self.image_path = cover_art_url
        self.album = album
        try:
            self.image = Image.open(urlopen(self.image_path))
        except (URLError, HTTPError) as error:
            raise URLError(f"Could not open {self.image_path} {error}") from error
        except ValueError as error:
            raise ValueError(f"Could not open {self.image_path} {error}") from error

        # convert the image to a numpy array
        self.image = self.image.convert("RGBA")
        self.pixels = np.array(self.image.getdata())

        # Find transparent pixels and store them in case we want to remove transparency
        self.transparent_pixels = self.pixels[:, 3] == 0
        self.pixels = self.pixels[:, :3]
        self.kmeans = None
        self.hexcodes = None
        self.is_colorblind_friendly = None

    def hexcodes_to_hsv(self):
        """Return ``self.hexcodes`` converted to HSV values."""

        if not self.hexcodes:
            raise ValueError("No hexcodes have been generated")

        hsv_colors = [
            colorsys.rgb_to_hsv(*mpl.colors.to_rgb(hexcode))
            for hexcode in self.hexcodes
        ]
        return hsv_colors

    @staticmethod
    def _filter_colors(
        colors: np.ndarray,
        light: bool = False,
        dark: bool = False,
        bold: bool = False,
        light_thresh: float = 0.6,
        dark_thresh: float = 0.4,
        bold_thresh: float = 0.6,
    ) -> np.ndarray:
        """Filter ``colors`` based on brightness and saturation."""

        if not (light or dark or bold):
            return colors

        hsv = np.array([colorsys.rgb_to_hsv(*c) for c in colors])
        mask = np.ones(len(colors), dtype=bool)
        if light and not dark:
            mask &= hsv[:, 2] >= light_thresh
        if dark and not light:
            mask &= hsv[:, 2] <= dark_thresh
        if bold:
            mask &= hsv[:, 1] >= bold_thresh
        filtered = colors[mask]
        return filtered if len(filtered) > 0 else colors

    def generate_cmap(self, n_colors=4, palette_name = None, random_state=None):
        """Generates a matplotlib ListedColormap from an image.

        Args:
            n_colors (int, optional): The number of colors in the ListedColormap. Defaults to 4.
            palette_name (str, optional): A name for your created palette. If None, defaults to the image name.
                Defaults to None.
            random_state (int, optional): A random seed for reproducing ListedColormaps.
                The k-means algorithm has a random initialization step and doesn't always converge on the same
                solution because of this. If None will be a different seed each time this method is called.
                Defaults to None.

        Returns:
            matplotlib.colors.ListedColormap: A matplotlib ListedColormap object.
        """
        # create a kmeans model
        self.kmeans = MiniBatchKMeans(n_clusters=n_colors, random_state=random_state, n_init=3)
        # fit the model to the pixels
        self.kmeans.fit(self.pixels)
        # get the cluster centers
        centroids = self.kmeans.cluster_centers_ / 255
        # return the palette
        if not palette_name:
            palette_name = self.album
        cmap = mpl.colors.ListedColormap(centroids, name=palette_name)

        # Handle 4 dimension RGBA colors
        cmap.colors = cmap.colors[:, :3]

        # Sort colors by hue
        cmap.colors = sorted(cmap.colors, key=lambda rgb: colorsys.rgb_to_hsv(*rgb))
        # Handle cases where all rgb values evaluate to 1 or 0. This is a temporary fix
        cmap.colors = np.where(np.isclose(cmap.colors, 1), 1 - 1e-6, cmap.colors)
        cmap.colors = np.where(np.isclose(cmap.colors, 0), 1e-6, cmap.colors)

        self.hexcodes = [mpl.colors.rgb2hex(c) for c in cmap.colors]
        self.is_colorblind_friendly = self.colorblind_friendly(cmap)
        return cmap

    def generate_optimal_cmap(self, max_colors=10, palette_name=None, random_state=None):
        """Generates an optimal matplotlib ListedColormap from an image by finding the optimal number of clusters using the elbow method.

        Useage:
            >>> img = ImageConverter("path/to/image.png")
            >>> cmaps, best_n_colors, ssd = img.generate_optimal_cmap()
            >>> # The optimal colormap
            >>> cmaps[best_n_colors]


        Args:
            max_colors (int, optional): _description_. Defaults to 10.
            palette_name (_type_, optional): _description_. Defaults to None.
            random_state (_type_, optional): _description_. Defaults to None.
            remove_background (_type_, optional): _description_. Defaults to None.

        Returns:
            dict: A dictionary of matplotlib ListedColormap objects.
            Keys are the number of colors (clusters). Values are ListedColormap objects.
            int: The optimal number of colors.
            dict: A dictionary of the sum of square distances from each point to the cluster center.
            Keys are the number of colors (clusters) and values are the SSD value.
        """
        ssd = dict()
        cmaps = dict()
        if not palette_name:
            palette_name = self.album
        for n_colors in range(2, max_colors + 1):
            cmap = self.generate_cmap(n_colors=n_colors, palette_name=palette_name, random_state=random_state)
            cmaps[n_colors] = cmap
            ssd[n_colors] = self.kmeans.inertia_

        best_n_colors = KneeLocator(list(ssd.keys()), list(ssd.values()), curve="convex", direction="decreasing").knee
        try:
            self.hexcodes = [mpl.colors.rgb2hex(c) for c in cmaps[best_n_colors].colors]
        except KeyError:
            # Kneed did not find an optimal point so we don't record any hex values
            self.hexcodes = None
        if best_n_colors in cmaps:
            self.is_colorblind_friendly = self.colorblind_friendly(cmaps[best_n_colors])
        return cmaps, best_n_colors, ssd
    
    def get_distinct_colors(
        self,
        cmap,
        n_colors,
        light: bool = False,
        dark: bool = False,
        bold: bool = False,
    ):
        """Get the most distinct colors from a colormap.

        Args:
            cmap (matplotlib.colors.ListedColormap): The colormap.
            n_colors (int): The number of distinct colors to get.
            light, dark, bold (bool): Apply brightness/saturation filters.

        Returns:
            list: A list of the most distinct RGB color tuples.
        """
        colors = np.array(cmap.colors)
        colors = self._filter_colors(colors, light=light, dark=dark, bold=bold)

        if len(colors) < n_colors:
            colors = np.array(cmap.colors)

        kmeans = KMeans(n_clusters=n_colors, random_state=0, n_init=1).fit(colors)
        distinct_colors = np.array(kmeans.cluster_centers_)
        distinct_cmap = ListedColormap(distinct_colors)

        return distinct_colors, distinct_cmap
    
    def generate_distinct_optimal_cmap(
        self,
        max_colors: int = 10,
        n_distinct_colors: int = 4,
        palette_name: Optional[str] = None,
        random_state: Optional[int] = None,
        *,
        light: bool = False,
        dark: bool = False,
        bold: bool = False,
    ):
        """Generates an optimal colormap and then picks the most distinct colors from it.

        Args:
            max_colors (int, optional): The maximum number of colors to consider for the colormap. Defaults to 10.
            n_distinct_colors (int, optional): The number of distinct colors to pick from the optimal colormap. Defaults to 4.
            palette_name (_type_, optional): The name of the palette to use. Defaults to None.
            random_state (_type_, optional): The seed for the random number generator. Defaults to None.
            light, dark, bold (bool, optional): Filter colors by brightness or
                saturation before measuring distinctness. ``light`` keeps bright
                colors, ``dark`` keeps dim colors and ``bold`` prefers saturated
                colors. Defaults to False.

        Returns:
            list: A list of the most distinct RGB color tuples.
            matplotlib.colors.ListedColormap: A colormap of the most distinct colors.
        """
        # Generate the optimal colormap
        cmaps, best_n_colors, ssd = self.generate_optimal_cmap(
            max_colors, palette_name, random_state
        )

        max_distinctness = 0
        best_distinct_colors = None
        best_distinct_cmap = None
        # Pick the most distinct colors from the optimal colormap
        for n_colors, cmap in cmaps.items():
            if len(cmap.colors) < n_distinct_colors:
                continue
            
            distinct_colors, distinct_cmap = self.get_distinct_colors(
                cmap, n_distinct_colors, light=light, dark=dark, bold=bold
            )

            # Calculate the total pairwise distance between the colors
            distinctness = np.sum(squareform(pdist(distinct_colors)))

            # If this set of colors is more distinct than the best so far, update the best
            if distinctness > max_distinctness:
                max_distinctness = distinctness
                best_distinct_colors = distinct_colors
                best_distinct_cmap = distinct_cmap
        
        best_distinct_colors = np.array(best_distinct_colors)
        self.hexcodes = [mpl.colors.rgb2hex(c) for c in best_distinct_colors]
        if best_distinct_cmap is not None:
            self.is_colorblind_friendly = self.colorblind_friendly(best_distinct_cmap)

        return best_distinct_colors, best_distinct_cmap

    @staticmethod
    def _hue_distinctness(colors: np.ndarray) -> float:
        """Return a metric representing the total hue separation."""

        hues = np.array([colorsys.rgb_to_hsv(*c)[0] for c in colors])
        diff = np.abs(hues[:, None] - hues[None, :])
        diff = np.minimum(diff, 1 - diff)
        return diff.sum()

    def get_hue_distinct_colors(self, cmap, n_colors):
        """Pick ``n_colors`` maximizing hue separation from ``cmap``."""

        colors = np.array(cmap.colors)
        hues = np.array([[colorsys.rgb_to_hsv(*c)[0]] for c in colors])
        kmeans = KMeans(n_clusters=n_colors, random_state=0, n_init=1).fit(hues)
        centers = kmeans.cluster_centers_.ravel()
        indices = [np.argmin(np.abs(hues.ravel() - c)) for c in centers]
        distinct_colors = colors[indices]
        distinct_cmap = ListedColormap(distinct_colors)
        return distinct_colors, distinct_cmap

    def generate_hue_distinct_optimal_cmap(
        self,
        max_colors: int = 10,
        n_distinct_colors: int = 4,
        palette_name: Optional[str] = None,
        random_state: Optional[int] = None,
        *,
        light: bool = False,
        dark: bool = False,
        bold: bool = False,
    ):
        """Generate a colormap maximizing hue distinction."""

        cmaps, _, _ = self.generate_optimal_cmap(max_colors, palette_name, random_state)

        best_distinct = 0
        best_colors = None
        best_cmap = None
        for cmap in cmaps.values():
            colors = np.array(cmap.colors)
            if len(colors) < n_distinct_colors:
                continue
            filtered = self._filter_colors(colors, light=light, dark=dark, bold=bold)
            if len(filtered) < n_distinct_colors:
                filtered = colors
            tmp_cmap = ListedColormap(filtered)
            distinct, dcmap = self.get_hue_distinct_colors(tmp_cmap, n_distinct_colors)
            d = self._hue_distinctness(distinct)
            if d > best_distinct:
                best_distinct = d
                best_colors = distinct
                best_cmap = dcmap

        if best_colors is None:
            raise ValueError("Unable to select distinct hues with the given parameters")

        best_colors = np.array(best_colors)
        self.hexcodes = [mpl.colors.rgb2hex(c) for c in best_colors]
        return best_colors, best_cmap

    def remove_transparent(self):
        """Removes the transparent pixels from an image array.

        Returns:
            None
        """
        self.pixels = self.pixels[~self.transparent_pixels]

    def display_with_colorbar(self, cmap):
        """
        Display an image with a colorbar.

        Parameters:
        cmap (matplotlib.colors.Colormap): The colormap to use.

        Returns:
        None
        """
        try:
            # Open the image from the URL
            with urlopen(self.image_path) as url:
                with Image.open(url) as img:
                    img_array = np.array(img)

            # Create the plot
            fig, ax = plt.subplots(figsize=(7, 5))
            ax.axis("off")
            im = ax.imshow(img_array, cmap=cmap)

            # Add a colorbar
            divider = make_axes_locatable(ax)
            cax = divider.append_axes("right", size="10%", pad=0.05)
            cb = fig.colorbar(im, cax=cax, orientation="vertical")
            cb.set_ticks([])

            # Display the plot
            plt.show()

        except Exception as e:
            print(f"Error displaying image with colorbar: {e}")

    def preview_palette(self, cmap):
        """Show the album cover alongside a sample plot using ``cmap``."""

        try:
            with urlopen(self.image_path) as url:
                with Image.open(url) as img:
                    img_array = np.array(img)

            fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10, 4))

            ax1.axis("off")
            ax1.imshow(img_array)
            divider = make_axes_locatable(ax1)
            cax = divider.append_axes("right", size="5%", pad=0.05)
            cb = fig.colorbar(mpl.cm.ScalarMappable(cmap=cmap), cax=cax)
            cb.set_ticks([])

            x = np.linspace(0, 10, 100)
            for i, color in enumerate(cmap.colors):
                ax2.plot(x, np.sin(x + i), color=color, linewidth=3)
            ax2.set_title("Sample Plot")
            ax2.set_xticks([])
            ax2.set_yticks([])

            plt.tight_layout()
            plt.show()

        except Exception as e:
            print(f"Error displaying preview: {e}")

    def colorblind_friendly(self, cmap, deficiency: str = "deuteranopia", threshold: float = 0.1) -> bool:
        """Return ``True`` if ``cmap`` remains distinct for a color vision deficiency.

        Parameters
        ----------
        cmap : matplotlib.colors.Colormap
            Colormap to evaluate.
        deficiency : str, optional
            One of ``"protanopia"``, ``"deuteranopia"`` or ``"tritanopia"``.
        threshold : float, optional
            Minimum distance between simulated colors.  Smaller values mark
            colors as indistinguishable.  Defaults to 0.1.
        """

        colors = getattr(cmap, "colors", [])
        return is_colorblind_friendly(colors, deficiency=deficiency, threshold=threshold)

    def save_palette(self, path: Optional[str] = None):
        """Save ``self.hexcodes`` and metadata and return the palette id.

        When ``path`` is ``None`` the palette is recorded only in
        ``index.json`` under ``PALETTE_DIR``.  If a path is supplied the
        hexcodes are also written to that location as JSON.  All palette
        metadata and hexcodes are stored in ``index.json`` so that palettes can
        easily be listed and loaded later.  Each palette is assigned a
        numerical ``id`` which can be used for listing, loading and deleting
        palettes.

        Returns
        -------
        int
            The ``id`` assigned to the saved palette.

        Raises
        ------
        ValueError
            If ``hexcodes`` have not been generated.
        """

        if not self.hexcodes:
            raise ValueError("No palette has been generated to save")

        # Load existing index to determine next id and upgrade if needed
        data = _load_index(assign_ids=True)

        next_id = max([entry.get("id", 0) for entry in data], default=0) + 1

        json_path = Path(path) if path else None

        if json_path:
            try:
                with json_path.open("w") as f:
                    json.dump(self.hexcodes, f)
            except OSError as e:
                print(f"Error saving palette to {json_path}: {e}")
                json_path = None

        # Update index metadata
        metadata = {
            "id": next_id,
            "artist": self.artist,
            "album": self.album,
            "n_colors": len(self.hexcodes),
            "image_url": self.image_path,
            "hexcodes": self.hexcodes,
            "path": str(json_path) if json_path else None,
        }

        data.append(metadata)
        with INDEX_FILE.open("w") as f:
            json.dump(data, f, indent=2)

        return next_id

    def load_palette(self, path: Union[str, Path]):
        """Load hexcodes from ``path`` and set ``self.hexcodes``.

        Parameters:
            path (str or Path): Path to a JSON file written by :meth:`save_palette`.

        Raises:
            FileNotFoundError: If the palette file does not exist.
            ValueError: If the palette cannot be parsed.
        """

        path = Path(path)

        try:
            with path.open("r") as f:
                self.hexcodes = json.load(f)
        except FileNotFoundError as e:
            raise FileNotFoundError(f"Palette file not found: {path}") from e
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid palette file: {e}") from e

    def load_palette_by_name(self, name: str):
        """Load a saved palette using its registered ``name``."""

        data = _load_index(assign_ids=True)
        if not data:
            raise FileNotFoundError("No saved palettes available")

        data.sort(key=lambda d: d.get("id", 0))

        for entry in data:
            if entry.get("name") == name:
                if entry.get("hexcodes"):
                    self.hexcodes = entry["hexcodes"]
                elif entry.get("path"):
                    self.load_palette(entry["path"])
                else:
                    raise FileNotFoundError(f"Palette data for '{name}' missing")
                self.image_path = entry.get("image_url", self.image_path)
                return

        raise FileNotFoundError(f"Saved palette '{name}' not found")

    def load_palette_by_id(self, palette_id: int):
        """Load a saved palette using its numeric ``id``."""

        data = _load_index(assign_ids=True)
        if not data:
            raise FileNotFoundError("No saved palettes available")

        for entry in data:
            if entry.get("id") == palette_id:
                if entry.get("hexcodes"):
                    self.hexcodes = entry["hexcodes"]
                elif entry.get("path"):
                    self.load_palette(entry["path"])
                else:
                    raise FileNotFoundError(f"Palette data for id {palette_id} missing")
                self.image_path = entry.get("image_url", self.image_path)
                self.artist = entry.get("artist", self.artist)
                self.album = entry.get("album", self.album)
                return

        raise FileNotFoundError(f"Saved palette id {palette_id} not found")

    @staticmethod
    def delete_palette(palette_id: int) -> bool:
        """Remove a palette from ``index.json`` and delete its file if present.

        Parameters
        ----------
        palette_id : int
            Numeric id of the palette to remove.

        Returns
        -------
        bool
            ``True`` if a palette was removed, ``False`` otherwise.
        """

        data = _load_index(assign_ids=True)
        if not data:
            return False

        remaining = []
        removed_entry = None
        for entry in data:
            if entry.get("id") == palette_id:
                removed_entry = entry
            else:
                remaining.append(entry)

        if removed_entry is None:
            return False

        with INDEX_FILE.open("w") as f:
            json.dump(remaining, f, indent=2)

        palette_path = removed_entry.get("path")
        if palette_path:
            try:
                Path(palette_path).unlink()
            except OSError:
                pass

        return True

    @staticmethod
    def list_palettes(page: int = 1, per_page: int = 10):
        """Return a paginated list of saved palette metadata."""

        data = _load_index(assign_ids=True)
        if not data:
            return []

        data.sort(key=lambda d: d.get("id", 0))

        start = max(0, (page - 1) * per_page)
        end = start + per_page
        return data[start:end]

    @staticmethod
    def find_palettes_by_color_count(n_colors: int, page: int = 1, per_page: int = 10):
        """Return saved palettes matching ``n_colors``."""

        data = _load_index(assign_ids=True)
        if not data:
            return []

        matches = [entry for entry in data if entry.get("n_colors") == n_colors]
        matches.sort(key=lambda d: d.get("id", 0))

        start = max(0, (page - 1) * per_page)
        end = start + per_page
        return matches[start:end]

    @staticmethod
    def pdf_file() -> Path:
        """Return the path to the stored palettes PDF."""

        return PALETTE_DIR / "palettes.pdf"

    @staticmethod
    def create_palettes_pdf(force: bool = False) -> Optional[Path]:
        """Generate a PDF listing saved palettes and return its path.

        The PDF is stored under ``PALETTE_DIR`` as ``palettes.pdf``. If the
        PDF already exists and is newer than ``index.json`` it is reused unless
        ``force`` is ``True``. Returns ``None`` when no palettes are saved.
        """

        data = _load_index(assign_ids=True)
        if not data:
            return None

        pdf_path = CoverPalette.pdf_file()

        if not force and pdf_path.exists():
            if pdf_path.stat().st_mtime >= INDEX_FILE.stat().st_mtime:
                return pdf_path

        from matplotlib.backends.backend_pdf import PdfPages

        per_page = 10
        with PdfPages(pdf_path) as pdf:
            for i in range(0, len(data), per_page):
                chunk = data[i : i + per_page]
                rows = len(chunk)

                fig, axes = plt.subplots(
                    rows,
                    3,
                    figsize=(8, rows),
                    gridspec_kw={"width_ratios": [1, 3, 2]},
                )

                axes_list = axes if rows > 1 else [axes]

                for (img_ax, bar_ax, text_ax), entry in zip(axes_list, chunk):
                    for ax in (img_ax, bar_ax, text_ax):
                        ax.axis("off")

                    hexcodes = entry.get("hexcodes") or []
                    cmap = ListedColormap([mpl.colors.to_rgb(h) for h in hexcodes])

                    gradient = np.linspace(0, 1, 256).reshape(1, -1)
                    bar_ax.imshow(gradient, aspect="auto", cmap=cmap)

                    artist = (entry.get("artist") or "").title()
                    album = (entry.get("album") or "").title()
                    pid = entry.get("id")
                    text = (
                        f"#{pid} {artist} - {album} "
                        f"({entry.get('n_colors')} colors)\n"
                        + " ".join(hexcodes)
                    )
                    text_ax.text(0, 0.5, text, va="center", ha="left", fontsize=8)

                    img_url = entry.get("image_url")
                    if img_url:
                        try:
                            with urlopen(img_url) as url:
                                with Image.open(url) as img:
                                    img_ax.imshow(img)
                        except Exception:
                            pass

                plt.tight_layout(pad=0.25)
                pdf.savefig(fig)
                plt.close(fig)

        return pdf_path
