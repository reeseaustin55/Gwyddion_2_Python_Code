"""Tkinter-based GUI for configuring and running batch jobs."""

from __future__ import absolute_import, print_function

import datetime
import json
import logging
import os
import threading

try:  # Python 2.7
    import Tkinter as tk
    import tkFileDialog
    import tkMessageBox
except ImportError:  # pragma: no cover - Python 3 fallback
    import tkinter as tk
    from tkinter import filedialog as tkFileDialog
    from tkinter import messagebox as tkMessageBox

try:  # Python 2.7
    import Queue as queue
except ImportError:  # pragma: no cover - Python 3 fallback
    import queue

from .config import (BatchConfig, VideoSettings, StabilizationSettings,
                     ProcessingOptions)
from .gwyddion_loader import import_gwyddion
from .processor import GwyddionBatchProcessor


DEFAULT_DATA_FOLDER = r'D:\AFM Images'
DEFAULT_FFMPEG_PATH = r"C:\\Program Files\\ffmpeg-2025-02-24-git-6232f416b1-full_build\\bin\\ffmpeg.exe"
SETTINGS_FILE = os.path.join(os.path.expanduser('~'), '.gwyddion_batch_gui.json')


class _QueueHandler(logging.Handler):
    """Forward logging messages to a ``Queue`` for UI display."""

    def __init__(self, message_queue):
        logging.Handler.__init__(self)
        self._queue = message_queue

    def emit(self, record):  # pragma: no cover - UI side effect
        try:
            message = self.format(record)
        except Exception:  # pragma: no cover - defensive
            message = record.getMessage()
        self._queue.put(message)


class BatchProcessorGUI(object):  # pragma: no cover - UI heavy
    """Simple GUI to collect batch processor settings and run a job."""

    def __init__(self):
        self.root = tk.Tk()
        self.root.title('Gwyddion Batch Processor')
        self.log_queue = queue.Queue()
        self.processing_thread = None

        self._defaults = self._load_persisted_settings()

        self._build_variables()
        self._build_ui()

    # ------------------------------------------------------------------ UI --
    def _load_persisted_settings(self):
        try:
            with open(SETTINGS_FILE, 'r') as handle:
                data = json.load(handle)
                if isinstance(data, dict):
                    return data
        except Exception:
            pass
        return {}

    def _save_persisted_settings(self, config):
        output_directory = self.output_dir_var.get()
        output_parent = ''
        if output_directory:
            normalized = output_directory.rstrip('\\/')
            if normalized:
                base_name = os.path.basename(normalized)
                if base_name and base_name.lower().startswith('output_'):
                    output_parent = os.path.dirname(normalized)
                else:
                    output_parent = normalized

        data = {
            'folder': self.folder_var.get(),
            'output_directory': output_directory,
            'output_parent': output_parent,
            'channels': self.channels_var.get(),
            'pixel_count': self.pixel_count_var.get(),
            'file_filter': self.filter_var.get(),
            'video': {
                'enabled': bool(self.video_enabled_var.get()),
                'duration': self.video_duration_var.get(),
                'split_scans': bool(self.video_split_var.get()),
                'uniform_frame_duration': bool(self.video_uniform_var.get()),
                'stabilization': {
                    'enabled': bool(self.stabilize_var.get()),
                    'max_displacement_percent': self.stabilize_percent_var.get(),
                },
            },
            'processing': {
                'flatten': bool(self.flatten_var.get()),
                'align_rows': bool(self.align_rows_var.get()),
                'align_method': self.align_method_var.get(),
                'align_degree': self.align_degree_var.get(),
                'remove_scars': bool(self.remove_scars_var.get()),
                'fix_zero': bool(self.fix_zero_var.get()),
                'export_stats': bool(self.stats_var.get()),
                'generate_acf': bool(self.acf_var.get()),
                'generate_psdf': bool(self.psdf_var.get()),
                'generate_angular_spectrum': bool(self.angular_var.get()),
            },
        }
        try:
            with open(SETTINGS_FILE, 'w') as handle:
                json.dump(data, handle, indent=2, sort_keys=True)
        except Exception:
            # Persistence is a convenience feature, so failures are ignored.
            pass

    def _build_variables(self):
        defaults = self._defaults
        folder_default = defaults.get('folder', DEFAULT_DATA_FOLDER)
        output_parent = defaults.get('output_parent')
        output_default = defaults.get('output_directory')
        if output_parent:
            output_default = self._default_output_for(output_parent)
        elif output_default:
            normalized = output_default.rstrip('\\/')
            if normalized:
                base_name = os.path.basename(normalized)
                if base_name and base_name.lower().startswith('output_'):
                    parent = os.path.dirname(normalized)
                    reference = parent or folder_default
                    output_default = self._default_output_for(reference)
        if not output_default:
            output_default = self._default_output_for(folder_default)

        self.folder_var = tk.StringVar(value=folder_default)
        self.output_dir_var = tk.StringVar(value=output_default)
        self.channels_var = tk.StringVar(value=defaults.get('channels', '0'))
        self.pixel_count_var = tk.StringVar(value=str(defaults.get('pixel_count', '1024')))
        self.filter_var = tk.StringVar(value=defaults.get('file_filter', '.ibw'))

        video_defaults = defaults.get('video', {})
        self.video_enabled_var = tk.IntVar(value=1 if video_defaults.get('enabled') else 0)
        self.video_duration_var = tk.StringVar(value=str(video_defaults.get('duration', '10')))
        self.video_split_var = tk.IntVar(value=1 if video_defaults.get('split_scans') else 0)
        self.video_uniform_var = tk.IntVar(
            value=1 if video_defaults.get('uniform_frame_duration') else 0)

        stabilization_defaults = video_defaults.get('stabilization', {})
        self.stabilize_var = tk.IntVar(value=1 if stabilization_defaults.get('enabled') else 0)
        percent_default = stabilization_defaults.get('max_displacement_percent', '5.0')
        self.stabilize_percent_var = tk.StringVar(value=str(percent_default))

        processing_defaults = defaults.get('processing', {})
        self.flatten_var = tk.IntVar(value=1 if processing_defaults.get('flatten', True) else 0)
        self.align_rows_var = tk.IntVar(value=1 if processing_defaults.get('align_rows', True) else 0)
        align_method = processing_defaults.get('align_method', 'polynomial')
        self.align_method_var = tk.StringVar(value=align_method)
        self.align_degree_var = tk.StringVar(value=str(processing_defaults.get('align_degree', 2)))
        self.remove_scars_var = tk.IntVar(value=1 if processing_defaults.get('remove_scars') else 0)
        self.fix_zero_var = tk.IntVar(value=1 if processing_defaults.get('fix_zero', True) else 0)
        self.stats_var = tk.IntVar(value=1 if processing_defaults.get('export_stats') else 0)
        self.acf_var = tk.IntVar(value=1 if processing_defaults.get('generate_acf') else 0)
        self.psdf_var = tk.IntVar(value=1 if processing_defaults.get('generate_psdf') else 0)
        self.angular_var = tk.IntVar(
            value=1 if processing_defaults.get('generate_angular_spectrum') else 0)

    def _build_ui(self):
        main = tk.Frame(self.root)
        main.pack(fill='both', expand=True, padx=10, pady=10)
        main.columnconfigure(1, weight=1)

        row = 0
        self._add_labeled_entry(main, 'Folder:', self.folder_var, row,
                                browse_command=self._browse_folder)
        row += 1
        output_entry = self._add_labeled_entry(main, 'Output directory:', self.output_dir_var, row)
        output_entry.configure(state='readonly')
        row += 1
        self._add_labeled_entry(main, 'Channels (comma separated):', self.channels_var, row)
        row += 1
        self._add_labeled_entry(main, 'Pixel count:', self.pixel_count_var, row)
        row += 1
        self._add_labeled_entry(main, 'File filter (e.g. .spm):', self.filter_var, row)
        row += 1

        self._video_entries = []
        self._stabilization_entries = []
        self._video_checkbuttons = []
        self._align_method_buttons = []
        self._align_degree_entry = None

        processing_frame = tk.LabelFrame(main, text='Image processing')
        processing_frame.grid(row=row, column=0, columnspan=3, sticky='nsew', pady=(10, 0))
        processing_frame.columnconfigure(1, weight=1)

        flatten_cb = tk.Checkbutton(processing_frame, text='Flattening', variable=self.flatten_var)
        flatten_cb.grid(row=0, column=0, columnspan=3, sticky='w')

        align_cb = tk.Checkbutton(
            processing_frame,
            text='Align rows',
            variable=self.align_rows_var,
            command=self._toggle_align_controls,
        )
        align_cb.grid(row=1, column=0, sticky='w')

        method_frame = tk.Frame(processing_frame)
        method_frame.grid(row=1, column=1, columnspan=2, sticky='w')
        median_rb = tk.Radiobutton(
            method_frame,
            text='Median of differences',
            variable=self.align_method_var,
            value='median',
            command=self._toggle_align_controls,
        )
        median_rb.pack(side='left', padx=(0, 10))
        polynomial_rb = tk.Radiobutton(
            method_frame,
            text='Polynomial',
            variable=self.align_method_var,
            value='polynomial',
            command=self._toggle_align_controls,
        )
        polynomial_rb.pack(side='left')
        self._align_method_buttons.extend([median_rb, polynomial_rb])

        self._align_degree_entry = self._add_labeled_entry(
            processing_frame,
            'Polynomial degree:',
            self.align_degree_var,
            2,
        )

        scars_cb = tk.Checkbutton(processing_frame, text='Scars remove', variable=self.remove_scars_var)
        scars_cb.grid(row=3, column=0, columnspan=3, sticky='w')

        fix_zero_cb = tk.Checkbutton(
            processing_frame,
            text='Fix zero (height channels only)',
            variable=self.fix_zero_var,
        )
        fix_zero_cb.grid(row=4, column=0, columnspan=3, sticky='w')

        stats_cb = tk.Checkbutton(processing_frame, text='Export stats file', variable=self.stats_var)
        stats_cb.grid(row=5, column=0, columnspan=3, sticky='w')

        acf_cb = tk.Checkbutton(processing_frame, text='Generate ACF image', variable=self.acf_var)
        acf_cb.grid(row=6, column=0, columnspan=3, sticky='w')

        psdf_cb = tk.Checkbutton(
            processing_frame,
            text='Generate 2D PSDF image',
            variable=self.psdf_var,
        )
        psdf_cb.grid(row=7, column=0, columnspan=3, sticky='w')

        angular_cb = tk.Checkbutton(
            processing_frame,
            text='Generate angular spectrum image',
            variable=self.angular_var,
        )
        angular_cb.grid(row=8, column=0, columnspan=3, sticky='w')

        row += 1

        video_frame = tk.LabelFrame(main, text='Video rendering')
        video_frame.grid(row=row, column=0, columnspan=3, sticky='nsew', pady=(10, 0))
        video_frame.columnconfigure(1, weight=1)

        video_cb = tk.Checkbutton(video_frame, text='Enable video stitching',
                                  variable=self.video_enabled_var,
                                  command=self._toggle_video_fields)
        video_cb.grid(row=0, column=0, columnspan=3, sticky='w')

        self._add_labeled_entry(
            video_frame,
            'Video duration (s):',
            self.video_duration_var,
            1,
            entry_list=self._video_entries,
        )
        uniform_cb = tk.Checkbutton(
            video_frame,
            text='Display each frame for the same duration',
            variable=self.video_uniform_var,
        )
        uniform_cb.grid(row=2, column=0, columnspan=3, sticky='w')
        self._video_checkbuttons.append(uniform_cb)
        split_cb = tk.Checkbutton(
            video_frame,
            text='Split UP/DOWN scans',
            variable=self.video_split_var,
        )
        split_cb.grid(row=3, column=0, columnspan=3, sticky='w')
        self._video_checkbuttons.append(split_cb)

        row += 1
        stab_frame = tk.LabelFrame(main, text='Video stabilization')
        stab_frame.grid(row=row, column=0, columnspan=3, sticky='nsew', pady=(10, 0))
        stab_frame.columnconfigure(1, weight=1)

        self._stabilize_checkbox = tk.Checkbutton(
            stab_frame,
            text='Enable stabilization',
            variable=self.stabilize_var,
            command=self._toggle_stabilization_fields,
        )
        self._stabilize_checkbox.grid(row=0, column=0, columnspan=3, sticky='w')

        self._add_labeled_entry(
            stab_frame,
            'Max drift (% width):',
            self.stabilize_percent_var,
            1,
            entry_list=self._stabilization_entries,
        )

        row += 1
        button_frame = tk.Frame(main)
        button_frame.grid(row=row, column=0, columnspan=3, pady=10, sticky='e')
        self.process_button = tk.Button(button_frame, text='Run Processing', command=self.start_processing)
        self.process_button.pack(side='right')

        row += 1
        log_frame = tk.LabelFrame(main, text='Status')
        log_frame.grid(row=row, column=0, columnspan=3, sticky='nsew')
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)

        self.log_text = tk.Text(log_frame, height=12, width=80, state='disabled')
        self.log_text.grid(row=0, column=0, sticky='nsew')

        scrollbar = tk.Scrollbar(log_frame, command=self.log_text.yview)
        scrollbar.grid(row=0, column=1, sticky='ns')
        self.log_text.configure(yscrollcommand=scrollbar.set)

        self._toggle_align_controls()
        self._toggle_video_fields()
        self._toggle_stabilization_fields()

    def _add_labeled_entry(self, master, label_text, variable, row,
                           browse_command=None, entry_list=None):
        label = tk.Label(master, text=label_text)
        label.grid(row=row, column=0, sticky='e', padx=(0, 5), pady=2)
        entry = tk.Entry(master, textvariable=variable)
        entry.grid(row=row, column=1, sticky='we', pady=2)
        if browse_command is not None:
            button = tk.Button(master, text='Browse...', command=browse_command)
            button.grid(row=row, column=2, padx=(5, 0), pady=2)
        if entry_list is not None:
            entry_list.append(entry)
        return entry

    def _default_output_for(self, folder):
        if not folder:
            folder = DEFAULT_DATA_FOLDER
        timestamp = datetime.datetime.now().strftime('output_%Y%m%d_%H%M%S')
        folder = folder.rstrip('\\/')
        if ('/' in folder) and ('\\' not in folder):
            return folder + '/' + timestamp
        path = os.path.join(folder, timestamp)
        if ('\\' in folder) and ('/' not in folder):
            path = path.replace('/', '\\')
        return path

    def _browse_folder(self):
        initial = self.folder_var.get() or DEFAULT_DATA_FOLDER
        path = tkFileDialog.askdirectory(initialdir=initial)
        if path:
            previous_folder = self.folder_var.get()
            current_output = self.output_dir_var.get()
            self.folder_var.set(path)
            if (not current_output) or (previous_folder and current_output.startswith(previous_folder)):
                self.output_dir_var.set(self._default_output_for(path))

    def _browse_output(self):
        initial = (self.output_dir_var.get()
                   or self.folder_var.get()
                   or DEFAULT_DATA_FOLDER)
        path = tkFileDialog.askdirectory(initialdir=initial)
        if path:
            self.output_dir_var.set(path)

    def _toggle_video_fields(self):
        state = tk.NORMAL if self.video_enabled_var.get() else tk.DISABLED
        for entry in self._video_entries:
            entry.configure(state=state)
        for checkbox in getattr(self, '_video_checkbuttons', []):
            checkbox.configure(state=state)
        if self.video_enabled_var.get():
            self._stabilize_checkbox.configure(state=tk.NORMAL)
        else:
            self.stabilize_var.set(0)
            self._stabilize_checkbox.configure(state=tk.DISABLED)
        self._toggle_stabilization_fields()

    def _toggle_stabilization_fields(self):
        video_enabled = bool(self.video_enabled_var.get())
        state = tk.NORMAL if (video_enabled and self.stabilize_var.get()) else tk.DISABLED
        for entry in self._stabilization_entries:
            entry.configure(state=state)
        if not video_enabled:
            self.stabilize_var.set(0)

    def _toggle_align_controls(self):
        enabled = bool(self.align_rows_var.get())
        state = tk.NORMAL if enabled else tk.DISABLED
        for button in self._align_method_buttons:
            button.configure(state=state)
        if self._align_degree_entry is not None:
            if enabled and self.align_method_var.get() == 'polynomial':
                degree_state = tk.NORMAL
            else:
                degree_state = tk.DISABLED
            self._align_degree_entry.configure(state=degree_state)

    # --------------------------------------------------------------- runtime --
    def start_processing(self):
        if self.processing_thread and self.processing_thread.is_alive():
            return
        try:
            config = self._build_config()
        except ValueError as exc:
            tkMessageBox.showerror('Invalid settings', str(exc))
            return

        self._append_log('Starting batch processing...')
        self.process_button.configure(state=tk.DISABLED)
        self.processing_thread = threading.Thread(target=self._run_processing, args=(config,))
        self.processing_thread.daemon = True
        self.processing_thread.start()
        self.root.after(100, self._poll_log_queue)

    def _build_config(self):
        folder = self.folder_var.get().strip()
        if not folder:
            raise ValueError('Folder path is required')

        channels_text = self.channels_var.get().strip()
        channels = []
        if channels_text:
            for part in channels_text.replace(';', ',').split(','):
                part = part.strip()
                if not part:
                    continue
                channels.append(int(part))
        if not channels:
            raise ValueError('Please specify at least one channel number')

        pixel_count = int(self.pixel_count_var.get())
        file_filter = self.filter_var.get().strip()
        if file_filter:
            if file_filter.lower() == 'all':
                file_filter = None
            else:
                if not file_filter.startswith('.'):
                    file_filter = '.' + file_filter
                file_filter = file_filter.lower()
        else:
            file_filter = None

        output_directory = self.output_dir_var.get().strip() or None

        video_enabled = bool(self.video_enabled_var.get())
        video_duration = self._parse_float(self.video_duration_var.get())
        if video_enabled and (video_duration is None or video_duration <= 0):
            raise ValueError('Video duration must be greater than zero when video stitching is enabled.')
        frame_rate = None
        split_scans = bool(self.video_split_var.get()) if video_enabled else False
        uniform_frame_duration = bool(self.video_uniform_var.get()) if video_enabled else False

        stabilization_enabled = video_enabled and bool(self.stabilize_var.get())
        percent_value = self._parse_float(self.stabilize_percent_var.get())
        if percent_value is None or percent_value < 0:
            percent_value = 0.0
        stabilization = StabilizationSettings(
            enabled=stabilization_enabled,
            max_displacement_percent=percent_value,
        )

        ffmpeg_path = DEFAULT_FFMPEG_PATH
        if not os.path.exists(ffmpeg_path):
            ffmpeg_path = 'ffmpeg'

        video_settings = VideoSettings(
            enabled=video_enabled,
            ffmpeg_path=ffmpeg_path,
            duration_seconds=video_duration,
            stabilization=stabilization,
            split_scans=split_scans,
            uniform_frame_duration=uniform_frame_duration,
        )

        try:
            align_degree = int(self.align_degree_var.get())
        except Exception:
            align_degree = 2
        processing_options = ProcessingOptions(
            flatten=bool(self.flatten_var.get()),
            align_rows=bool(self.align_rows_var.get()),
            align_method=self.align_method_var.get(),
            align_degree=align_degree,
            remove_scars=bool(self.remove_scars_var.get()),
            fix_zero=bool(self.fix_zero_var.get()),
            export_stats=bool(self.stats_var.get()),
            generate_acf=bool(self.acf_var.get()),
            generate_psdf=bool(self.psdf_var.get()),
            generate_angular_spectrum=bool(self.angular_var.get()),
        )

        config = BatchConfig(
            folder_path=folder,
            channel_numbers=channels,
            pixel_count=pixel_count,
            file_filter=file_filter,
            output_directory=output_directory,
            video_settings=video_settings,
            run_timestamp=datetime.datetime.now(),
            processing_options=processing_options,
        )

        self.output_dir_var.set(config.output_directory)
        self._save_persisted_settings(config)
        return config

    def _parse_float(self, value):
        value = value.strip() if value else ''
        if not value:
            return None
        return float(value)

    def _run_processing(self, config):
        handler = _QueueHandler(self.log_queue)
        formatter = logging.Formatter('%(levelname)s: %(message)s')
        handler.setFormatter(formatter)

        logger = logging.getLogger('GwyddionBatchGUI')
        logger.setLevel(logging.INFO)
        logger.addHandler(handler)

        try:
            gwy = import_gwyddion(config.gwyddion_paths, logger=logger)
            processor = GwyddionBatchProcessor(gwy, logger=logger)
            result = processor.process_folder(config)
            self.log_queue.put('Processing finished: %d/%d successes' %
                               (result.get('processed', 0), result.get('total', 0)))
            if result.get('output_directory'):
                self.log_queue.put('Processed images saved to %s' % result['output_directory'])
            for channel, details in sorted(result.get('per_channel', {}).items()):
                self.log_queue.put('Channel %d: %d/%d images saved' % (
                    channel,
                    details.get('processed', 0),
                    details.get('total', 0)))
            for channel, videos in sorted(result.get('video_paths', {}).items()):
                if isinstance(videos, dict):
                    for kind, entries in sorted(videos.items()):
                        if isinstance(entries, dict):
                            for direction, path in sorted(entries.items()):
                                self.log_queue.put('Channel %d %s %s video written to %s'
                                                   % (channel, kind, direction, path))
                        else:
                            self.log_queue.put('Channel %d %s video written to %s'
                                               % (channel, kind, entries))
                else:
                    self.log_queue.put('Channel %d video written to %s'
                                       % (channel, videos))
        except Exception as exc:  # pragma: no cover - user feedback
            error_message = 'ERROR: %s' % exc
            self.log_queue.put(error_message)
            self.log_queue.put(('__ERROR__', str(exc)))
        finally:
            logger.removeHandler(handler)
            self.log_queue.put('__COMPLETE__')

    def _poll_log_queue(self):
        try:
            while True:
                message = self.log_queue.get_nowait()
                if message == '__COMPLETE__':
                    self.processing_thread = None
                    self.process_button.configure(state=tk.NORMAL)
                elif isinstance(message, tuple):
                    tag, payload = message
                    if tag == '__ERROR__':
                        tkMessageBox.showerror('Processing failed', payload)
                else:
                    self._append_log(message)
        except queue.Empty:
            pass
        if self.processing_thread is not None:
            self.root.after(100, self._poll_log_queue)

    def _append_log(self, message):
        self.log_text.configure(state='normal')
        self.log_text.insert('end', message + '\n')
        self.log_text.see('end')
        self.log_text.configure(state='disabled')

    def run(self):
        self.root.mainloop()

