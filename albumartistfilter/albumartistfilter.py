"""Album Artist Filter 1.0.0 -- stable release with playback-safe model updates.

Rhythmbox's native PropertyModel cannot safely be constructed for ALBUM_ARTIST.
Use GTK TreeViews for Album Artist and Album; never ask the hidden native Album
widget to filter songs.  Instead, filter the native browser's Genre/search
output model by *both* the selected Album Artist and selected Album(s).

The displayed artists and albums derive from the native Album property's base
query model (upstream of album filtering). Native Artist and Album selections
are cleared while this plugin is active, and restored on deactivation. The
replacement panes track changes to Rhythmbox's browser-views preference.
"""

import logging

from gi.repository import GObject, GLib, Gtk, Pango, Peas, RB

LOG = logging.getLogger(__name__)
ALBUM_ARTIST = RB.RhythmDBPropType.ALBUM_ARTIST
ARTIST = RB.RhythmDBPropType.ARTIST
ALBUM = RB.RhythmDBPropType.ALBUM
UNKNOWN_ALBUM = "(Unknown Album)"
UNKNOWN = '(Unknown Album Artist)'


class AlbumArtistBrowser(GObject.Object, Peas.Activatable):
    __gtype_name__ = 'AlbumArtistBrowserSafe'
    object = GObject.Property(type=GObject.Object)

    def __init__(self):
        super().__init__()
        self._source = None
        self._entry_view = None
        self._db = None
        self._browser = None
        self._artist = None
        self._native_album = None
        self._old_album_selection = []
        self._album_was_visible = False
        self._album_no_show_all = False
        self._old_artist_selection = []
        self._was_visible = False
        self._no_show_all = False
        self._scroller = None
        self._tree = None
        self._rows = None
        self._selection = None
        self._album_scroller = None
        self._album_tree = None
        self._album_rows = None
        self._album_selection = None
        self._album_updating = False
        self._album_prop_model = None
        self._album_prop_handler = None
        self._base_model = None
        self._base_handlers = []
        self._albums_all = {}
        self._artist_albums = {}
        self._updating = False
        self._selected = None
        self._selected_albums = set()
        self._browser_handler = None
        self._views_handler = None
        self._model = None
        self._model_handlers = []
        self._refresh_timer = None
        self._read_idle = None
        self._filter_idle = None
        self._generation = 0
        self._filter_generation = 0
        self._counts = {}
        self._total = 0
        self._iterator = None
        self._filtered = None
        # Store the most recently *rendered* album-artist/album summary.
        # The native query model also emits row-changed for unrelated track
        # metadata, such as play count/last played during Album Shuffle.
        # Those updates must not replace the song-list model, since doing so
        # erases the playing-row highlight and can disrupt scrolling.
        self._last_display_state = None
        self._force_song_model_refresh = True
        self._active = False

    @staticmethod
    def _artist_name(entry):
        return (entry.get_string(ALBUM_ARTIST) or '').strip() or UNKNOWN

    def do_activate(self):
        if self._active:
            return
        try:
            shell = self.object
            source = shell.props.library_source
            if source is None:
                LOG.warning('Album Artist Filter: library source not ready')
                return
            artist = next((p for p in source.get_property_views()
                           if p.props.prop == ARTIST), None)
            if artist is None:
                LOG.warning('Album Artist Filter: artist column not found')
                return
            native_album = next((p for p in source.get_property_views()
                                 if p.props.prop == ALBUM), None)
            if native_album is None:
                LOG.warning('Album Artist Filter: album column not found')
                return
            browser = artist.get_parent()
            if not isinstance(browser, RB.LibraryBrowser):
                LOG.warning('Album Artist Filter: unexpected browser container')
                return
            entry_view = source.get_entry_view()
            native_model = browser.props.output_model
            if entry_view is None or native_model is None:
                LOG.warning('Album Artist Filter: song model unavailable')
                return

            self._source = source
            self._entry_view = entry_view
            self._db = shell.props.db
            self._browser = browser
            self._artist = artist
            self._native_album = native_album
            self._old_album_selection = list(native_album.get_selection() or [])
            self._album_was_visible = native_album.get_visible()
            self._album_no_show_all = native_album.get_no_show_all()
            self._album_prop_model = native_album.get_model()
            self._was_visible = artist.get_visible()
            self._no_show_all = artist.get_no_show_all()
            self._old_artist_selection = list(artist.get_selection() or [])

            self._rows = Gtk.ListStore(str, str)
            tree = Gtk.TreeView(model=self._rows)
            tree.set_headers_visible(True)
            column = Gtk.TreeViewColumn('Album Artist')
            renderer = Gtk.CellRendererText()
            renderer.set_property('ellipsize', Pango.EllipsizeMode.MIDDLE)
            column.pack_start(renderer, True)
            column.add_attribute(renderer, 'text', 0)
            column.set_sizing(Gtk.TreeViewColumnSizing.AUTOSIZE)
            tree.append_column(column)
            self._tree = tree
            selection = tree.get_selection()
            selection.set_mode(Gtk.SelectionMode.SINGLE)
            self._selection = selection
            selection.connect('changed', self._on_selection)

            scroller = Gtk.ScrolledWindow()
            scroller.set_policy(Gtk.PolicyType.AUTOMATIC,
                                Gtk.PolicyType.AUTOMATIC)
            scroller.set_hexpand(True)
            scroller.set_vexpand(True)
            scroller.add(tree)
            self._scroller = scroller

            # Replace only the VISIBLE album widget. The original remains
            # registered with RBLibraryBrowser; setting its ALBUM selection
            # still invokes Rhythmbox's real native album filter.
            self._album_rows = Gtk.ListStore(str, str, bool)
            album_tree = Gtk.TreeView(model=self._album_rows)
            album_tree.set_headers_visible(True)
            album_column = Gtk.TreeViewColumn('Album')
            album_renderer = Gtk.CellRendererText()
            album_renderer.set_property('ellipsize', Pango.EllipsizeMode.MIDDLE)
            album_column.pack_start(album_renderer, True)
            album_column.add_attribute(album_renderer, 'text', 0)
            album_column.set_sizing(Gtk.TreeViewColumnSizing.AUTOSIZE)
            album_tree.append_column(album_column)
            self._album_tree = album_tree
            album_selection = album_tree.get_selection()
            album_selection.set_mode(Gtk.SelectionMode.MULTIPLE)
            self._album_selection = album_selection
            album_selection.connect('changed', self._on_album_selection)

            album_scroller = Gtk.ScrolledWindow()
            album_scroller.set_policy(Gtk.PolicyType.AUTOMATIC,
                                      Gtk.PolicyType.AUTOMATIC)
            album_scroller.set_hexpand(True)
            album_scroller.set_vexpand(True)
            album_scroller.add(album_tree)
            self._album_scroller = album_scroller

            position = browser.get_children().index(artist)
            browser.pack_start(scroller, True, True, 0)
            browser.reorder_child(scroller, position)
            scroller.show_all()
            native_album_position = browser.get_children().index(native_album)
            browser.pack_start(album_scroller, True, True, 0)
            browser.reorder_child(album_scroller, native_album_position)
            album_scroller.show_all()
            # Rhythmbox may call show_all() on ancestor containers; a pane
            # omitted from browser preferences must stay hidden.
            album_scroller.set_no_show_all(True)
            artist.set_no_show_all(True)
            artist.hide()
            native_album.set_no_show_all(True)
            native_album.hide()
            self._active = True

            # Keep both hidden native selectors clear. They are retained only
            # to preserve Genre/search's normal browser model and to restore
            # their original state when the plugin is turned off.
            if self._old_artist_selection:
                artist.reset()
            if self._old_album_selection:
                native_album.reset()

            self._browser_handler = browser.connect(
                'notify::output-model', self._on_native_model_changed)
            # Native RBLibraryBrowser re-shows Artist/Album whenever the
            # browser-views preference is set. Reapply our replacements *after*
            # its setter has updated the native widgets (GObject::notify).
            self._views_handler = browser.connect(
                'notify::browser-views', self._on_browser_views_changed)
            self._sync_browser_visibility()
            self._attach_model(browser.props.output_model)
            self._album_prop_handler = self._album_prop_model.connect(
                'notify::query-model', self._on_album_base_changed)
            self._attach_base_model(self._album_prop_model.props.query_model)
            self._schedule_refresh()
            LOG.info('Album Artist Filter 1.0.0 activated')
        except Exception:
            LOG.exception('Album Artist Filter: activation failed')
            self.do_deactivate()

    def _on_browser_views_changed(self, *_args):
        if not self._active:
            return
        try:
            self._sync_browser_visibility()
        except Exception:
            LOG.exception('Album Artist Filter: unable to apply browser preferences')

    def _sync_browser_visibility(self):
        """Mirror browser options, substituting Album Artist for native Artist.

        RBLibraryBrowser always shows native Artist and conditionally shows
        native Album in update_browser_views_visibility().  Those explicit
        GtkWidget.show() calls undo our hides on every preference change.
        """
        if not self._active or self._browser is None:
            return
        views = self._browser.props.browser_views or ''
        show_albums = 'albums' in views

        # Keep the single installed pair of replacements; never add panes on
        # settings updates. The native Genre pane is managed by Rhythmbox.
        if self._artist is not None:
            self._artist.hide()
        if self._native_album is not None:
            self._native_album.hide()
        if self._scroller is not None:
            self._scroller.show()
        if self._album_scroller is not None:
            if show_albums:
                self._album_scroller.show()
            else:
                self._album_scroller.hide()

        # If the user switches to "Genres and Artists", hidden album
        # selections must not continue filtering the song list invisibly.
        if not show_albums and self._selected_albums:
            self._selected_albums.clear()
            self._sync_visible_album_selection()
            self._schedule_filter()

    def _on_native_model_changed(self, *_args):
        if not self._active:
            return
        try:
            self._attach_model(self._browser.props.output_model)
            self._attach_base_model(self._album_prop_model.props.query_model)
            self._schedule_refresh()
        except Exception:
            LOG.exception('Album Artist Filter: model changed')

    def _on_album_base_changed(self, *_args):
        if not self._active:
            return
        try:
            self._attach_base_model(self._album_prop_model.props.query_model)
            self._schedule_refresh()
        except Exception:
            LOG.exception('Album Artist Filter: album base changed')

    def _on_base_rows_changed(self, *_args):
        # Insertions/deletions may replace a track with another carrying the
        # same album tags and counts; don't mistake them for metadata-only
        # row-changed signals.
        if _args and _args[-1] != 'row-changed':
            self._force_song_model_refresh = True
        self._schedule_refresh()

    def _attach_base_model(self, model):
        if model is self._base_model:
            return
        self._detach_base_model()
        self._base_model = model
        if model is not None:
            for signal in ('row-inserted', 'row-deleted', 'row-changed'):
                self._base_handlers.append(
                    model.connect(signal, self._on_base_rows_changed, signal))

    def _detach_base_model(self):
        if self._base_model is not None:
            for handler in self._base_handlers:
                try:
                    self._base_model.disconnect(handler)
                except (TypeError, RuntimeError):
                    pass
        self._base_handlers = []
        self._base_model = None

    def _on_native_rows_changed(self, *_args):
        if _args and _args[-1] != 'row-changed':
            self._force_song_model_refresh = True
        self._schedule_refresh()

    def _attach_model(self, model):
        if model is self._model:
            return
        self._detach_model()
        self._model = model
        self._force_song_model_refresh = True
        if model is not None:
            for signal in ('row-inserted', 'row-deleted', 'row-changed'):
                self._model_handlers.append(
                    model.connect(signal, self._on_native_rows_changed, signal))

    def _detach_model(self):
        if self._model is not None:
            for handler in self._model_handlers:
                try:
                    self._model.disconnect(handler)
                except (TypeError, RuntimeError):
                    pass
        self._model_handlers = []
        self._model = None

    def _schedule_refresh(self):
        if not self._active:
            return
        # Coalesce bursts of signals emitted during genre/album selection,
        # database loading, or search.
        if self._refresh_timer is None:
            self._refresh_timer = GLib.timeout_add(120, self._refresh)

    def _refresh(self):
        self._refresh_timer = None
        self._generation += 1
        generation = self._generation
        if self._read_idle is not None:
            GLib.source_remove(self._read_idle)
            self._read_idle = None
        # Do not cancel an in-flight Album Artist/Album selection filter yet.
        # A playback metadata row-changed could arrive while the user-chosen
        # filter is being constructed.  If the contents are unchanged, leave
        # that in-flight operation alone; _schedule_filter() cancels it if a
        # real library/browser change requires rebuilding.
        self._counts = {}
        self._albums_all = {}
        self._artist_albums = {}
        self._total = 0
        self._iterator = None
        if not self._active or self._base_model is None:
            return GLib.SOURCE_REMOVE
        try:
            self._iterator = iter(self._base_model)
            self._read_idle = GLib.idle_add(self._read_chunk, generation)
        except Exception:
            LOG.exception('Album Artist Filter: refresh failed')
        return GLib.SOURCE_REMOVE

    def _read_chunk(self, generation):
        if not self._active or generation != self._generation:
            return GLib.SOURCE_REMOVE
        try:
            for _ in range(400):
                try:
                    row = next(self._iterator)
                except StopIteration:
                    self._iterator = None
                    break
                entry = row[0]
                name = self._artist_name(entry)
                album = entry.get_string(ALBUM) or ''
                self._counts[name] = self._counts.get(name, 0) + 1
                self._albums_all[album] = self._albums_all.get(album, 0) + 1
                artist_albums = self._artist_albums.setdefault(name, {})
                artist_albums[album] = artist_albums.get(album, 0) + 1
                self._total += 1
            if self._iterator is not None:
                return GLib.SOURCE_CONTINUE
            self._display_counts()
        except Exception:
            LOG.exception('Album Artist Filter: unable to read album artists')
            self._iterator = None
        finally:
            if self._iterator is None:
                self._read_idle = None
        return GLib.SOURCE_REMOVE

    def _display_counts(self):
        if self._rows is None:
            return
        # A native row-changed occurs for ordinary playback bookkeeping.
        # Compare the *complete* tag/grouping summary rather than blindly
        # rebuilding our custom song model after each such event.  The inner
        # artist->album map matters when two artists have equal totals.
        # These dictionaries are not mutated once a scan completes: _refresh
        # creates new ones, so retaining references needs no extra copies.
        state = (self._counts, self._albums_all,
                 self._artist_albums, self._total)
        if (not self._force_song_model_refresh and
                self._last_display_state is not None and
                state == self._last_display_state):
            return
        self._last_display_state = state
        self._force_song_model_refresh = False
        # Refreshing the view must NOT itself trigger a user filter change.
        self._updating = True
        try:
            self._rows.clear()
            self._rows.append([f'All {len(self._counts)} album artists ({self._total})', ''])
            selected_index = 0
            for idx, name in enumerate(sorted(self._counts, key=str.casefold), 1):
                self._rows.append([f'{name} ({self._counts[name]})', name])
                if name == self._selected:
                    selected_index = idx
            if self._selected is not None and selected_index == 0:
                self._selected = None
                self._selected_albums.clear()
            self._selection.select_path(
                Gtk.TreePath.new_from_indices([selected_index]))
        finally:
            self._updating = False
        self._display_albums()
        self._schedule_filter()

    def _on_selection(self, selection):
        if not self._active or self._updating:
            return
        model, iterator = selection.get_selected()
        if iterator is None:
            return
        raw_name = model.get_value(iterator, 1)
        new_selection = raw_name or None
        if new_selection != self._selected:
            self._selected = new_selection
            # Selecting a different Album Artist resets Album to All.
            self._selected_albums.clear()
            self._display_albums()
            self._schedule_filter()

    def _sync_visible_album_selection(self):
        """Restore our album selection after a count/model refresh."""
        if not self._active or self._album_selection is None or self._album_rows is None:
            return
        self._album_updating = True
        try:
            self._album_selection.unselect_all()
            selected_any = False
            for index, row in enumerate(self._album_rows):
                if not row[2] and row[1] in self._selected_albums:
                    self._album_selection.select_path(
                        Gtk.TreePath.new_from_indices([index]))
                    selected_any = True
            if not selected_any and len(self._album_rows):
                self._album_selection.select_path(
                    Gtk.TreePath.new_from_indices([0]))
        finally:
            self._album_updating = False

    def _display_albums(self):
        if self._album_rows is None or self._album_selection is None:
            return
        counts = (self._albums_all if self._selected is None else
                  self._artist_albums.get(self._selected, {}))
        # A genre/search change may make some selected albums disappear.
        self._selected_albums.intersection_update(counts)
        self._album_updating = True
        try:
            self._album_rows.clear()
            self._album_rows.append([
                f'All {len(counts)} albums ({sum(counts.values())})', '', True])
            for name in sorted(counts, key=str.casefold):
                display_name = name or UNKNOWN_ALBUM
                self._album_rows.append(
                    [f'{display_name} ({counts[name]})', name, False])
        finally:
            self._album_updating = False
        self._sync_visible_album_selection()

    def _on_album_selection(self, selection):
        if not self._active or self._album_updating:
            return
        model, paths = selection.get_selected_rows()
        values = set()
        all_selected = False
        for path in paths:
            iterator = model.get_iter(path)
            if model.get_value(iterator, 2):
                all_selected = True
            else:
                values.add(model.get_value(iterator, 1))
        if all_selected or not values:
            values.clear()
            if not (len(paths) == 1 and all_selected):
                self._sync_album_all_selection()
        if values != self._selected_albums:
            self._selected_albums = values
            self._schedule_filter()

    def _sync_album_all_selection(self):
        self._album_updating = True
        try:
            self._album_selection.unselect_all()
            self._album_selection.select_path(
                Gtk.TreePath.new_from_indices([0]))
        finally:
            self._album_updating = False

    def _cancel_filter(self):
        self._filter_generation += 1
        if self._filter_idle is not None:
            GLib.source_remove(self._filter_idle)
            self._filter_idle = None

    def _schedule_filter(self):
        self._cancel_filter()
        if not self._active or self._model is None:
            return
        # Native output already reflects Genre and text search.  Apply the
        # two custom selectors together, independently of Rhythmbox's hidden
        # Album widget, whose selection did not reliably update that output.
        if self._selected is None and not self._selected_albums:
            self._install_model(self._model)
            return
        generation = self._filter_generation
        try:
            matching = RB.RhythmDBQueryModel.new_empty(self._db)
            it = iter(self._model)
            self._filter_idle = GLib.idle_add(
                self._filter_chunk, generation, matching, it,
                self._selected, frozenset(self._selected_albums))
        except Exception:
            LOG.exception('Album Artist Filter: could not start filter')
            self._install_model(self._model)

    def _filter_chunk(self, generation, matching, entries,
                      artist_name, selected_albums):
        if not self._active or generation != self._filter_generation:
            return GLib.SOURCE_REMOVE
        try:
            for _ in range(300):
                try:
                    row = next(entries)
                except StopIteration:
                    self._filter_idle = None
                    self._filtered = matching
                    self._install_model(matching)
                    return GLib.SOURCE_REMOVE
                entry = row[0]
                if (artist_name is None or self._artist_name(entry) == artist_name) and (
                        not selected_albums or (entry.get_string(ALBUM) or '') in selected_albums):
                    matching.add_entry(entry, -1)
            return GLib.SOURCE_CONTINUE
        except Exception:
            LOG.exception('Album Artist Filter: filter failed')
            self._filter_idle = None
            self._install_model(self._model)
            return GLib.SOURCE_REMOVE

    def _install_model(self, model):
        if not self._active or model is None:
            return
        try:
            # Keep the visible songs *and* the source playback model aligned.
            # Native RBBrowserSource ordinarily does exactly these two things
            # when its output-model changes.
            self._entry_view.set_model(model)
            self._source.props.query_model = model
            self._source.notify_filter_changed()
            if model is self._model:
                self._filtered = None
        except Exception:
            LOG.exception('Album Artist Filter: could not update song list')

    def do_deactivate(self):
        self._active = False
        self._generation += 1
        self._cancel_filter()
        if self._refresh_timer is not None:
            GLib.source_remove(self._refresh_timer)
            self._refresh_timer = None
        if self._read_idle is not None:
            GLib.source_remove(self._read_idle)
            self._read_idle = None
        self._iterator = None
        if self._browser is not None and self._browser_handler is not None:
            try:
                self._browser.disconnect(self._browser_handler)
            except (TypeError, RuntimeError):
                pass
        self._browser_handler = None
        if self._browser is not None and self._views_handler is not None:
            try:
                self._browser.disconnect(self._views_handler)
            except (TypeError, RuntimeError):
                pass
        self._views_handler = None
        if self._album_prop_model is not None and self._album_prop_handler is not None:
            try:
                self._album_prop_model.disconnect(self._album_prop_handler)
            except (TypeError, RuntimeError):
                pass
        self._album_prop_handler = None
        self._album_prop_model = None
        self._detach_base_model()
        self._detach_model()
        if self._scroller is not None:
            self._scroller.destroy()
        if self._album_scroller is not None:
            self._album_scroller.destroy()
        self._album_scroller = None
        self._album_tree = None
        self._album_rows = None
        self._album_selection = None
        self._scroller = None
        self._tree = None
        self._rows = None
        self._selection = None
        self._selected = None
        self._selected_albums.clear()
        self._filtered = None
        # Restore native widgets according to the user's CURRENT browser
        # preference, not the one that was active when the plugin loaded.
        # Rhythmbox always includes native Artist and includes Album only
        # when browser-views contains "albums".
        current_views = (self._browser.props.browser_views or '') if self._browser is not None else ''
        if self._native_album is not None:
            self._native_album.set_no_show_all(self._album_no_show_all)
            if 'albums' in current_views:
                self._native_album.show()
            else:
                self._native_album.hide()
            if 'albums' in current_views:
                try:
                    self._native_album.set_selection(self._old_album_selection)
                except Exception:
                    LOG.exception('Album Artist Filter: could not restore Album selection')
        if self._artist is not None:
            self._artist.set_no_show_all(self._no_show_all)
            self._artist.show()
            if self._old_artist_selection:
                try:
                    self._artist.set_selection(self._old_artist_selection)
                except Exception:
                    LOG.exception('Album Artist Filter: could not restore Artist selection')
        # If the native browser hasn't emitted another output-model change,
        # restore the native model explicitly before dropping our references.
        if self._browser is not None and self._entry_view is not None:
            try:
                native = self._browser.props.output_model
                if native is not None:
                    self._entry_view.set_model(native)
                    self._source.props.query_model = native
                    self._source.notify_filter_changed()
            except Exception:
                LOG.exception('Album Artist Filter: could not restore song model')
        self._source = None
        self._entry_view = None
        self._db = None
        self._browser = None
        self._artist = None
        self._native_album = None
        self._old_artist_selection = []
        self._old_album_selection = []
        self._albums_all = {}
        self._artist_albums = {}
        self._counts = {}
        self._last_display_state = None
        self._total = 0
