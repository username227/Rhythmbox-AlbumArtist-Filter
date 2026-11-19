"""Album Artist Filter for Rhythmbox playlist browsers (experimental).

Companion to Album Artist Filter 1.0.0: the original plugin continues to manage
Music; this plugin manages only static and automatic playlist browser widgets.
The underlying playlist membership and source query_model are NEVER replaced.
Only the RBEntryView's displayed query model is filtered by album artist/album.
"""

import logging

from gi.repository import GObject, GLib, Gtk, Pango, Peas, RB

LOG = logging.getLogger('rhythmbox-albumartist-playlists')
ALBUM_ARTIST = RB.RhythmDBPropType.ALBUM_ARTIST
ARTIST = RB.RhythmDBPropType.ARTIST
ALBUM = RB.RhythmDBPropType.ALBUM
UNKNOWN = '(Unknown Album Artist)'
UNKNOWN_ALBUM = '(Unknown Album)'


class _PlaylistBrowser:
    """An independent browser/filter state for exactly one playlist source."""

    def __init__(self, shell, source):
        self.shell = shell
        self.source = source
        self.db = shell.props.db
        self.entry_view = None
        self.browser = None
        self.native_artist = None
        self.native_album = None
        self.artist_scroller = None
        self.artist_tree = None
        self.artist_rows = None
        self.artist_selection = None
        self.album_scroller = None
        self.album_tree = None
        self.album_rows = None
        self.album_selection = None
        self.old_artist_selection = []
        self.old_album_selection = []
        self.old_artist_visible = False
        self.old_album_visible = False
        self.old_artist_no_show_all = False
        self.old_album_no_show_all = False
        self.native_album_prop_model = None
        self.native_output = None
        self.base_model = None
        self.filtered = None
        self.counts = {}
        self.album_counts = {}
        self.artist_albums = {}
        self.total = 0
        self.current_artist = None
        self.current_albums = set()
        self.last_summary = None
        self.force_song_refresh = True
        self.updating = False
        self.updating_albums = False
        self.handlers = []
        self.model_handlers = []
        self.base_handlers = []
        self.refresh_id = None
        self.read_id = None
        self.filter_id = None
        self.read_generation = 0
        self.filter_generation = 0
        self.active = False
        self._reader = None
        self._setup()

    @staticmethod
    def _artist(entry):
        return (entry.get_string(ALBUM_ARTIST) or '').strip() or UNKNOWN

    def _connect(self, object_, signal, callback):
        self.handlers.append((object_, object_.connect(signal, callback)))

    @staticmethod
    def _pane(title, multi=False):
        rows = Gtk.ListStore(str, str, bool)
        view = Gtk.TreeView(model=rows)
        view.set_headers_visible(True)
        col = Gtk.TreeViewColumn(title)
        text = Gtk.CellRendererText()
        text.set_property('ellipsize', Pango.EllipsizeMode.MIDDLE)
        col.pack_start(text, True)
        col.add_attribute(text, 'text', 0)
        col.set_sizing(Gtk.TreeViewColumnSizing.AUTOSIZE)
        view.append_column(col)
        selection = view.get_selection()
        selection.set_mode(Gtk.SelectionMode.MULTIPLE if multi else Gtk.SelectionMode.SINGLE)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroll.set_hexpand(True)
        scroll.set_vexpand(True)
        scroll.add(view)
        return scroll, view, rows, selection

    def _setup(self):
        source = self.source
        views = source.get_property_views() or []
        artist = next((p for p in views if p.props.prop == ARTIST), None)
        album = next((p for p in views if p.props.prop == ALBUM), None)
        if artist is None or album is None:
            raise RuntimeError('Playlist property browser has no Artist/Album views')
        browser = artist.get_parent()
        if not isinstance(browser, RB.LibraryBrowser) or album.get_parent() is not browser:
            raise RuntimeError('Playlist Artist/Album widgets have unexpected parents')
        view = source.get_entry_view()
        if view is None or browser.props.output_model is None:
            raise RuntimeError('Playlist entry view/browser model unavailable')
        self.entry_view = view
        self.browser = browser
        self.native_artist = artist
        self.native_album = album
        self.native_album_prop_model = album.get_model()
        if self.native_album_prop_model is None:
            raise RuntimeError('Playlist Album property model unavailable')
        self.old_artist_selection = list(artist.get_selection() or [])
        self.old_album_selection = list(album.get_selection() or [])
        self.old_artist_visible = artist.get_visible()
        self.old_album_visible = album.get_visible()
        self.old_artist_no_show_all = artist.get_no_show_all()
        self.old_album_no_show_all = album.get_no_show_all()

        a = self._pane('Album Artist')
        self.artist_scroller, self.artist_tree, self.artist_rows, self.artist_selection = a
        b = self._pane('Album', multi=True)
        self.album_scroller, self.album_tree, self.album_rows, self.album_selection = b
        self.artist_selection.connect('changed', self._on_artist_changed)
        self.album_selection.connect('changed', self._on_album_changed)

        # Same replacement technique as Album Artist Filter 1.0.0: leave the
        # native property widgets registered, but never display or select them.
        try:
            pos_artist = browser.get_children().index(artist)
            browser.pack_start(self.artist_scroller, True, True, 0)
            browser.reorder_child(self.artist_scroller, pos_artist)
            self.artist_scroller.show_all()
            pos_album = browser.get_children().index(album)
            browser.pack_start(self.album_scroller, True, True, 0)
            browser.reorder_child(self.album_scroller, pos_album)
            self.album_scroller.show_all()
            self.artist_scroller.set_no_show_all(True)
            self.album_scroller.set_no_show_all(True)
            artist.set_no_show_all(True)
            album.set_no_show_all(True)
            artist.hide()
            album.hide()
            self.active = True
            if self.old_artist_selection:
                artist.reset()
            if self.old_album_selection:
                album.reset()
            self._connect(browser, 'notify::output-model', self._on_native_changed)
            self._connect(browser, 'notify::browser-views', self._on_views_changed)
            self._connect(self.native_album_prop_model, 'notify::query-model', self._on_base_changed)
            self._connect(source, 'deleted', self._on_deleted)
            self._set_native_model(browser.props.output_model)
            self._set_base_model(self.native_album_prop_model.props.query_model)
            self._sync_visibility()
            self._schedule_refresh()
        except Exception:
            self.close()
            raise

    def _on_deleted(self, *_args):
        # The owning plugin listens for the same signal and removes us from
        # its registry.  Shut down here to prevent stale source callbacks.
        self.close()

    def _on_views_changed(self, *_args):
        if self.active:
            self._sync_visibility()

    def _sync_visibility(self):
        if not self.active:
            return
        views = self.browser.props.browser_views or ''
        self.native_artist.hide()
        self.native_album.hide()
        self.artist_scroller.show()
        if 'albums' in views:
            self.album_scroller.show()
        else:
            self.album_scroller.hide()
            if self.current_albums:
                self.current_albums.clear()
                self._sync_album_selection()
                self._schedule_filter()

    def _on_native_changed(self, *_args):
        if not self.active:
            return
        self._set_native_model(self.browser.props.output_model)
        self._set_base_model(self.native_album_prop_model.props.query_model)
        self._schedule_refresh()
        self._schedule_filter()

    def _on_base_changed(self, *_args):
        if self.active:
            self._set_base_model(self.native_album_prop_model.props.query_model)
            self._schedule_refresh()

    def _set_native_model(self, model):
        if model is self.native_output:
            return
        for object_, handler in self.model_handlers:
            try: object_.disconnect(handler)
            except (TypeError, RuntimeError): pass
        self.model_handlers.clear()
        self.native_output = model
        self.force_song_refresh = True
        if model is not None:
            for signal in ('row-inserted', 'row-deleted', 'row-changed'):
                h = model.connect(signal, self._on_native_row, signal)
                self.model_handlers.append((model, h))

    def _set_base_model(self, model):
        if model is self.base_model:
            return
        for object_, handler in self.base_handlers:
            try: object_.disconnect(handler)
            except (TypeError, RuntimeError): pass
        self.base_handlers.clear()
        self.base_model = model
        if model is not None:
            for signal in ('row-inserted', 'row-deleted', 'row-changed'):
                h = model.connect(signal, self._on_base_row, signal)
                self.base_handlers.append((model, h))

    def _on_native_row(self, *_args):
        if _args and _args[-1] != 'row-changed':
            self.force_song_refresh = True
        self._schedule_refresh()

    def _on_base_row(self, *_args):
        if _args and _args[-1] != 'row-changed':
            self.force_song_refresh = True
        self._schedule_refresh()

    def _schedule_refresh(self):
        if self.active and self.refresh_id is None:
            self.refresh_id = GLib.timeout_add(120, self._refresh)

    def _refresh(self):
        self.refresh_id = None
        if not self.active:
            return GLib.SOURCE_REMOVE
        self.read_generation += 1
        generation = self.read_generation
        if self.read_id is not None:
            GLib.source_remove(self.read_id)
            self.read_id = None
        self.counts, self.album_counts, self.artist_albums = {}, {}, {}
        self.total = 0
        if self.base_model is None:
            return GLib.SOURCE_REMOVE
        try:
            self._reader = iter(self.base_model)
            self.read_id = GLib.idle_add(self._read_chunk, generation)
        except Exception:
            LOG.exception('Cannot iterate playlist browser base model')
        return GLib.SOURCE_REMOVE

    def _read_chunk(self, generation):
        if not self.active or generation != self.read_generation:
            return GLib.SOURCE_REMOVE
        try:
            for _ in range(400):
                try:
                    row = next(self._reader)
                except StopIteration:
                    self._reader = None
                    break
                entry = row[0]
                artist = self._artist(entry)
                album = entry.get_string(ALBUM) or ''
                self.counts[artist] = self.counts.get(artist, 0) + 1
                self.album_counts[album] = self.album_counts.get(album, 0) + 1
                grouped = self.artist_albums.setdefault(artist, {})
                grouped[album] = grouped.get(album, 0) + 1
                self.total += 1
            if self._reader is not None:
                return GLib.SOURCE_CONTINUE
            self._display_counts()
        except Exception:
            LOG.exception('Failed reading Album Artist tags for playlist')
            self._reader = None
        finally:
            if self._reader is None:
                self.read_id = None
        return GLib.SOURCE_REMOVE

    def _display_counts(self):
        state = (self.counts, self.album_counts, self.artist_albums, self.total)
        if not self.force_song_refresh and self.last_summary == state:
            return
        self.last_summary = state
        self.force_song_refresh = False
        self.updating = True
        try:
            self.artist_rows.clear()
            self.artist_rows.append([f'All {len(self.counts)} album artists ({self.total})', '', True])
            selected = 0
            for index, artist in enumerate(sorted(self.counts, key=str.casefold), 1):
                self.artist_rows.append([f'{artist} ({self.counts[artist]})', artist, False])
                if artist == self.current_artist:
                    selected = index
            if selected == 0 and self.current_artist is not None:
                self.current_artist = None
                self.current_albums.clear()
            self.artist_selection.select_path(Gtk.TreePath.new_from_indices([selected]))
        finally:
            self.updating = False
        self._display_albums()
        self._schedule_filter()

    def _on_artist_changed(self, selection):
        if not self.active or self.updating:
            return
        model, iterator = selection.get_selected()
        if iterator is None:
            return
        artist = model.get_value(iterator, 1) or None
        if artist != self.current_artist:
            self.current_artist = artist
            self.current_albums.clear()
            self._display_albums()
            self._schedule_filter()

    def _sync_album_selection(self):
        if self.album_selection is None:
            return
        self.updating_albums = True
        try:
            self.album_selection.unselect_all()
            matched = False
            for index, row in enumerate(self.album_rows):
                if not row[2] and row[1] in self.current_albums:
                    self.album_selection.select_path(Gtk.TreePath.new_from_indices([index]))
                    matched = True
            if not matched and len(self.album_rows):
                self.album_selection.select_path(Gtk.TreePath.new_from_indices([0]))
        finally:
            self.updating_albums = False

    def _display_albums(self):
        counts = (self.album_counts if self.current_artist is None else
                  self.artist_albums.get(self.current_artist, {}))
        self.current_albums.intersection_update(counts)
        self.updating_albums = True
        try:
            self.album_rows.clear()
            self.album_rows.append([f'All {len(counts)} albums ({sum(counts.values())})', '', True])
            for album in sorted(counts, key=str.casefold):
                self.album_rows.append([f'{album or UNKNOWN_ALBUM} ({counts[album]})', album, False])
        finally:
            self.updating_albums = False
        self._sync_album_selection()

    def _on_album_changed(self, selection):
        if not self.active or self.updating_albums:
            return
        model, paths = selection.get_selected_rows()
        albums = set()
        all_selected = False
        for path in paths:
            iterator = model.get_iter(path)
            if model.get_value(iterator, 2):
                all_selected = True
            else:
                albums.add(model.get_value(iterator, 1))
        if all_selected or not albums:
            albums.clear()
            if not (len(paths) == 1 and all_selected):
                self.updating_albums = True
                try:
                    self.album_selection.unselect_all()
                    self.album_selection.select_path(Gtk.TreePath.new_from_indices([0]))
                finally:
                    self.updating_albums = False
        if albums != self.current_albums:
            self.current_albums = albums
            self._schedule_filter()

    def _cancel_filter(self):
        self.filter_generation += 1
        if self.filter_id is not None:
            GLib.source_remove(self.filter_id)
            self.filter_id = None

    def _schedule_filter(self):
        self._cancel_filter()
        if not self.active or self.native_output is None:
            return
        if self.current_artist is None and not self.current_albums:
            self._set_displayed_model(self.native_output)
            return
        generation = self.filter_generation
        try:
            output = RB.RhythmDBQueryModel.new_empty(self.db)
            iterator = iter(self.native_output)
            self.filter_id = GLib.idle_add(self._filter_chunk, generation, output,
                iterator, self.current_artist, frozenset(self.current_albums))
        except Exception:
            LOG.exception('Could not start Album Artist playlist filter')
            self._set_displayed_model(self.native_output)

    def _filter_chunk(self, generation, output, iterator, artist, albums):
        if not self.active or generation != self.filter_generation:
            return GLib.SOURCE_REMOVE
        try:
            for _ in range(300):
                try:
                    row = next(iterator)
                except StopIteration:
                    self.filter_id = None
                    self._set_displayed_model(output)
                    return GLib.SOURCE_REMOVE
                entry = row[0]
                if ((artist is None or self._artist(entry) == artist) and
                        (not albums or (entry.get_string(ALBUM) or '') in albums)):
                    output.add_entry(entry, -1)
            return GLib.SOURCE_CONTINUE
        except Exception:
            self.filter_id = None
            LOG.exception('Album Artist playlist filtering failed')
            self._set_displayed_model(self.native_output)
            return GLib.SOURCE_REMOVE

    def _set_displayed_model(self, model):
        if not self.active or model is None:
            return
        try:
            if self.entry_view.props.model is not model:
                self.entry_view.set_model(model)
            self.filtered = model if model is not self.native_output else None
            # Do not set source.props.query_model: that is Rhythmbox's playing
            # model and (for static playlists) must not become our view-only
            # subset. Native Genre/search filtering remains Rhythmbox's own.
        except Exception:
            LOG.exception('Could not install playlist-visible Album Artist model')

    def close(self):
        self.active = False
        self.read_generation += 1
        self._cancel_filter()
        for key in ('refresh_id', 'read_id'):
            source_id = getattr(self, key)
            if source_id is not None:
                GLib.source_remove(source_id)
                setattr(self, key, None)
        for object_, handler in self.handlers + self.model_handlers + self.base_handlers:
            try: object_.disconnect(handler)
            except (TypeError, RuntimeError): pass
        self.handlers.clear()
        self.model_handlers.clear()
        self.base_handlers.clear()
        self._reader = None
        # Restore the native displayed model, without modifying the saved
        # playlist or its source playback model.
        if self.entry_view is not None and self.browser is not None:
            try:
                native = self.browser.props.output_model
                if native is not None:
                    self.entry_view.set_model(native)
            except (TypeError, RuntimeError):
                pass
        if self.artist_scroller is not None:
            try: self.artist_scroller.destroy()
            except (TypeError, RuntimeError): pass  # Playlist may already be disposed.
            self.artist_scroller = None
        if self.album_scroller is not None:
            try: self.album_scroller.destroy()
            except (TypeError, RuntimeError): pass
            self.album_scroller = None
        if self.native_artist is not None:
            try:
                self.native_artist.set_no_show_all(self.old_artist_no_show_all)
                self.native_artist.show()
                if self.old_artist_selection:
                    self.native_artist.set_selection(self.old_artist_selection)
            except (TypeError, RuntimeError):
                pass
        if self.native_album is not None:
            try:
                self.native_album.set_no_show_all(self.old_album_no_show_all)
                if self.browser is not None and 'albums' in (self.browser.props.browser_views or ''):
                    self.native_album.show()
                else:
                    self.native_album.hide()
                if self.old_album_selection:
                    self.native_album.set_selection(self.old_album_selection)
            except (TypeError, RuntimeError):
                pass
        handler = getattr(self, 'owner_handler', None)
        if handler is not None and self.source is not None:
            try: self.source.disconnect(handler)
            except (TypeError, RuntimeError): pass
            self.owner_handler = None
        self.filtered = None
        self.native_output = None
        self.base_model = None
        self.entry_view = None
        self.browser = None
        self.native_artist = None
        self.native_album = None
        self.source = None


class AlbumArtistPlaylistPlugin(GObject.Object, Peas.Activatable):
    __gtype_name__ = 'AlbumArtistPlaylistPluginExperimental'
    object = GObject.Property(type=GObject.Object)

    def __init__(self):
        super().__init__()
        self._contexts = {}
        self._manager = None
        self._added_handler = None

    def _add(self, source):
        types = [RB.StaticPlaylistSource]
        if getattr(RB, 'AutoPlaylistSource', None) is not None:
            types.append(RB.AutoPlaylistSource)
        if source in self._contexts or not isinstance(source, tuple(types)):
            return
        try:
            context = _PlaylistBrowser(self.object, source)
            self._contexts[source] = context
            # Remove closed playlists from our registry as soon as Rhythmbox
            # deletes them, including generated shuffle playlist swaps.
            context.owner_handler = source.connect('deleted', self._on_playlist_deleted)
            LOG.info('Album Artist playlist filter installed in %s', source.props.name)
        except Exception:
            LOG.exception('Album Artist playlist filter not available for %s',
                          getattr(source.props, 'name', '(unknown)'))

    def _on_playlist_added(self, _manager, source):
        self._add(source)

    def _on_playlist_deleted(self, source, *_args):
        context = self._contexts.pop(source, None)
        if context is not None:
            context.close()

    def do_activate(self):
        self._manager = self.object.props.playlist_manager
        self._added_handler = self._manager.connect('playlist-added', self._on_playlist_added)
        for source in self._manager.get_playlists():
            self._add(source)
        LOG.info('Album Artist Playlist Filter enabled for %d playlists', len(self._contexts))

    def do_deactivate(self):
        if self._manager is not None and self._added_handler is not None:
            try: self._manager.disconnect(self._added_handler)
            except (TypeError, RuntimeError): pass
        self._added_handler = None
        self._manager = None
        for context in list(self._contexts.values()):
            context.close()
        self._contexts.clear()
