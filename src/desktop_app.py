"""Native Windows GUI. Inference stays in the existing isolated CUDA worker."""
import argparse
import ctypes
import json
import os
import queue
import time
import tkinter as tk
from tkinter import ttk,filedialog,messagebox,scrolledtext
from pathlib import Path
from PIL import Image,ImageTk,ImageOps
from app_common import app_root,save_json,video_files
from desktop_controller import DesktopController
from video_preview import VideoPreview,WIDTH,HEIGHT

LABELS={'match':'自动保留','manual_kept':'手动保留','review':'待确认','no_match':'未匹配',
        'no_person':'未检测到人','error':'异常','manual_excluded':'已排除'}
BG='#f3f5f9'
INK='#182338'
MUTED='#617088'
BLUE='#2563eb'


class App:
    def __init__(self,window,root=None):
        self.window=window
        self.root=Path(root or app_root())
        self.controller=DesktopController(self.root)
        self.player=VideoPreview()
        self.closing=False
        self.playing=False
        self.scrubbing=False
        self.selected_key=None
        self.preview_source=None
        self.preview_image=None
        self.preview_photo=None
        self.row_map={}
        self.logs=[]
        self.backup=None
        self.filter_name='已保留'
        self.controls=[]
        config=self.root/'runtime/gui_settings.json'
        settings={}
        if config.exists():
            try:settings=json.loads(config.read_text(encoding='utf-8'))
            except (ValueError,OSError):pass
        self.paths={key:tk.StringVar(value=settings.get(key,str(default))) for key,default in {
            'reference':self.root/'input/reference_video','source':self.root/'input/waiting_for_filter_videos',
            'output':self.root/'output'}.items()}
        self.mode=tk.StringVar(value=settings.get('mode','标准（推荐）'))
        self.archive=tk.BooleanVar(value=settings.get('archive',True))
        self.refresh=tk.BooleanVar(value=False)
        self.search=tk.StringVar()
        self.status=tk.StringVar(value='选择目录后，点击「开始筛选」')
        self.detail=tk.StringVar(value='选择左侧视频，查看匹配人物和完整画面')
        self.progress_text=tk.StringVar(value='尚未开始')
        self.device=tk.StringVar(value='本机运行 · 视频不上传')
        self.seek=tk.DoubleVar(value=0)
        self.time_text=tk.StringVar(value='00:00 / 00:00')
        self.report_text=tk.StringVar(value='结果与复核')
        self._build()
        try:
            if self.controller.latest():
                self._loaded()
        except (OSError,ValueError,KeyError):
            self.status.set('历史结果未能读取，可点击「打开历史结果」选择报告')
        self.window.after(100,self._poll)

    def _build(self):
        w=self.window
        w.title('人物视频筛选')
        width=min(1320,w.winfo_screenwidth()-60)
        height=min(880,w.winfo_screenheight()-100)
        w.geometry(f'{width}x{height}')
        w.minsize(min(1100,width),min(760,height))
        w.configure(bg=BG)
        w.protocol('WM_DELETE_WINDOW',self.close)
        style=ttk.Style()
        style.theme_use('clam')
        style.configure('.',font=('Microsoft YaHei UI',10),background=BG,foreground=INK)
        style.configure('TFrame',background=BG)
        style.configure('Card.TFrame',background='white')
        style.configure('TLabel',background=BG)
        style.configure('Card.TLabel',background='white')
        style.configure('Muted.TLabel',foreground=MUTED)
        style.configure('TButton',padding=(12,7),background='white',borderwidth=1)
        style.map('TButton',background=[('active','#eaf0fc')])
        style.configure('Primary.TButton',background=BLUE,foreground='white',borderwidth=0,padding=(24,9))
        style.map('Primary.TButton',background=[('disabled','#a5b9dc'),('active','#1d4ed8')],foreground=[('disabled','white')])
        style.configure('Selected.TButton',background='#dce8ff',foreground='#1749b1')
        style.configure('Treeview',background='white',fieldbackground='white',rowheight=33,borderwidth=0,font=('Microsoft YaHei UI',10))
        style.configure('Treeview.Heading',background='#edf1f7',foreground=MUTED,padding=(8,9),font=('Microsoft YaHei UI',10,'bold'))
        style.map('Treeview',background=[('selected','#dce8ff')],foreground=[('selected',INK)])
        style.configure('Horizontal.TProgressbar',background=BLUE,troughcolor='#e3e8f0',borderwidth=0)
        main=ttk.Frame(w,padding=(24,18))
        main.pack(fill='both',expand=True)
        header=ttk.Frame(main)
        header.pack(fill='x',pady=(0,14))
        title=ttk.Frame(header)
        title.pack(side='left')
        ttk.Label(title,text='人物视频筛选',font=('Microsoft YaHei UI',23,'bold')).pack(anchor='w')
        ttk.Label(title,textvariable=self.device,style='Muted.TLabel').pack(anchor='w',pady=(3,0))
        ttk.Button(header,text='打开输出目录',command=self.open_output).pack(side='right',padx=(8,0))
        ttk.Button(header,text='打开历史结果',command=self.choose_report).pack(side='right')
        setup=ttk.Frame(main,style='Card.TFrame',padding=(18,13))
        setup.pack(fill='x',pady=(0,12))
        setup.columnconfigure(1,weight=1)
        self.counts={}
        for i,(key,label) in enumerate([('reference','参考视频目录'),('source','待筛选目录'),('output','输出目录')]):
            ttk.Label(setup,text=label,style='Card.TLabel',width=14).grid(row=i,column=0,sticky='w',pady=5)
            entry=ttk.Entry(setup,textvariable=self.paths[key])
            entry.grid(row=i,column=1,sticky='ew',padx=(5,10),pady=5)
            entry.bind('<FocusOut>',lambda event:self.update_counts())
            button=ttk.Button(setup,text='选择文件夹',command=lambda k=key:self.browse(k))
            button.grid(row=i,column=2,pady=5)
            self.controls.extend([entry,button])
            count=tk.StringVar(value='')
            self.counts[key]=count
            ttk.Label(setup,textvariable=count,style='Card.TLabel',foreground=MUTED,width=10).grid(row=i,column=3,padx=(12,0))
        options=ttk.Frame(main)
        options.pack(fill='x',pady=(0,9))
        self.start_button=ttk.Button(options,text='开始筛选',style='Primary.TButton',command=self.start)
        self.start_button.pack(side='left')
        self.stop_button=ttk.Button(options,text='停止',command=self.stop,state='disabled')
        self.stop_button.pack(side='left',padx=8)
        mode=ttk.Combobox(options,textvariable=self.mode,values=['标准（推荐）','更严格','更宽松（多选）'],state='readonly',width=17)
        mode.pack(side='left',padx=(10,12))
        archive=ttk.Checkbutton(options,text='归档旧输出，避免混入本轮',variable=self.archive)
        archive.pack(side='left',padx=(0,12))
        refresh=ttk.Checkbutton(options,text='重新分析',variable=self.refresh)
        refresh.pack(side='left')
        self.controls.extend([mode,archive,refresh])
        ttk.Button(options,text='查看参考视频',command=self.show_references).pack(side='right')
        progress=ttk.Frame(main)
        progress.pack(fill='x',pady=(0,12))
        ttk.Label(progress,textvariable=self.status,foreground=BLUE).pack(anchor='w')
        self.progress=ttk.Progressbar(progress,mode='determinate')
        self.progress.pack(fill='x',pady=(7,4))
        ttk.Label(progress,textvariable=self.progress_text,style='Muted.TLabel').pack(anchor='w')
        panes=ttk.Panedwindow(main,orient='horizontal')
        panes.pack(fill='both',expand=True)
        left=ttk.Frame(panes,style='Card.TFrame',padding=(12,10))
        right=ttk.Frame(panes,style='Card.TFrame',padding=(12,10))
        panes.add(left,weight=3)
        panes.add(right,weight=2)
        ttk.Label(left,textvariable=self.report_text,style='Card.TLabel',font=('Microsoft YaHei UI',12,'bold')).pack(anchor='w',pady=(0,8))
        filters=ttk.Frame(left,style='Card.TFrame')
        filters.pack(fill='x')
        self.filter_buttons={}
        for label in ['已保留','待确认','全部','未匹配','异常']:
            button=ttk.Button(filters,text=label,command=lambda l=label:self.filter(l))
            button.pack(side='left',padx=(0,4))
            self.filter_buttons[label]=button
        searchrow=ttk.Frame(left,style='Card.TFrame')
        searchrow.pack(fill='x',pady=8)
        ttk.Label(searchrow,text='搜索文件名',style='Card.TLabel').pack(side='left',padx=(0,8))
        ttk.Entry(searchrow,textvariable=self.search).pack(side='left',fill='x',expand=True)
        self.search.trace_add('write',lambda *_:self.render_rows())
        tableframe=ttk.Frame(left,style='Card.TFrame')
        tableframe.pack(fill='both',expand=True)
        self.table=ttk.Treeview(tableframe,columns=('status','file','time'),show='headings',selectmode='extended')
        for key,label,width in [('status','结果',105),('file','视频文件',325),('time','出现位置',85)]:
            self.table.heading(key,text=label)
            self.table.column(key,width=width,minwidth=70,stretch=key=='file')
        scroll=ttk.Scrollbar(tableframe,orient='vertical',command=self.table.yview)
        self.table.configure(yscrollcommand=scroll.set)
        self.table.pack(side='left',fill='both',expand=True)
        scroll.pack(side='right',fill='y')
        self.table.bind('<<TreeviewSelect>>',self.selected)
        self.table.bind('<Double-1>',lambda _:self.toggle_play())
        self.table.tag_configure('review',foreground='#a56a09')
        self.table.tag_configure('match',foreground='#16805b')
        self.table.tag_configure('manual_kept',foreground='#16805b')
        self.table.tag_configure('error',foreground='#c43c46')
        actions=ttk.Frame(left,style='Card.TFrame')
        actions.pack(fill='x',pady=(10,0))
        self.keep_button=ttk.Button(actions,text='保留选中',command=lambda:self.review('keep'))
        self.keep_button.pack(side='left')
        self.exclude_button=ttk.Button(actions,text='排除选中',command=lambda:self.review('exclude'))
        self.exclude_button.pack(side='left',padx=6)
        ttk.Label(actions,text='Ctrl / Shift 可多选',style='Card.TLabel',foreground=MUTED).pack(side='right')
        tabs=ttk.Notebook(right)
        tabs.pack(fill='both',expand=True)
        preview=ttk.Frame(tabs,style='Card.TFrame',padding=8)
        logtab=ttk.Frame(tabs,style='Card.TFrame')
        tabs.add(preview,text='视频与人物预览')
        tabs.add(logtab,text='运行日志')
        self.preview_title=ttk.Label(preview,text='选择视频后可在这里播放',style='Card.TLabel',wraplength=440)
        self.preview_title.pack(anchor='w',pady=(4,8))
        self.canvas=tk.Canvas(preview,bg='#142033',height=270,highlightthickness=0)
        self.canvas.pack(fill='both',expand=True)
        self.canvas.bind('<Configure>',lambda _:self.render_image())
        playback=ttk.Frame(preview,style='Card.TFrame')
        playback.pack(fill='x',pady=(8,3))
        self.play_button=ttk.Button(playback,text='播放视频',command=self.toggle_play)
        self.play_button.pack(side='left')
        ttk.Button(playback,text='外部打开',command=self.open_video).pack(side='left',padx=6)
        ttk.Label(playback,textvariable=self.time_text,style='Card.TLabel',foreground=MUTED).pack(side='right')
        self.slider=ttk.Scale(preview,from_=0,to=60,variable=self.seek)
        self.slider.pack(fill='x',pady=(0,8))
        self.slider.bind('<ButtonPress-1>',lambda _:setattr(self,'scrubbing',True))
        self.slider.bind('<ButtonRelease-1>',self.release_seek)
        ttk.Label(preview,textvariable=self.detail,style='Card.TLabel',wraplength=420,foreground=MUTED,justify='left').pack(anchor='w')
        self.log_widget=scrolledtext.ScrolledText(logtab,font=('Consolas',9),wrap='word',bg='#152034',fg='#e1e9f5',borderwidth=0,state='disabled')
        self.log_widget.pack(fill='both',expand=True)
        footer=ttk.Frame(main)
        footer.pack(fill='x',pady=(10,0))
        ttk.Label(footer,text='衣着看不清的片段请检查「待确认」。排除操作会备份输出文件，原视频不变。',style='Muted.TLabel').pack(side='left')
        ttk.Button(footer,text='打开复核报告',command=self.open_report).pack(side='right')
        ttk.Button(footer,text='打开备份',command=self.open_backup).pack(side='right',padx=6)
        self.update_counts()

    def browse(self,key):
        initial=self.paths[key].get()
        chosen=filedialog.askdirectory(title='选择'+{'reference':'参考视频目录','source':'待筛选目录','output':'输出目录'}[key],initialdir=initial if Path(initial).is_dir() else self.root)
        if chosen:self.paths[key].set(chosen);self.update_counts()

    def update_counts(self):
        for key,var in self.paths.items():
            try:self.counts[key].set(f'{len(video_files(var.get()))} 段视频' if Path(var.get()).is_dir() else '目录不存在')
            except OSError:self.counts[key].set('无法读取')

    def start(self):
        try:
            threshold={'标准（推荐）':.72,'更严格':.78,'更宽松（多选）':.68}[self.mode.get()]
            self.controller.start(*(self.paths[k].get() for k in ('reference','source','output')),
                threshold=threshold,refresh=self.refresh.get(),archive=self.archive.get())
            save_json(self.root/'runtime/gui_settings.json',{
                **{k:v.get() for k,v in self.paths.items()},'mode':self.mode.get(),'archive':self.archive.get()})
        except Exception as exc:
            messagebox.showerror('无法开始',str(exc),parent=self.window)
            return
        self.player.stop();self.playing=False
        self.preview_image=None;self.render_image()
        self.filter('全部')
        self.render_rows()
        self.set_running(True)
        self.progress.configure(value=0,maximum=self.controller.summary['total'])
        self.status.set('正在准备筛选，请稍候…')
        self.progress_text.set('读取模型和参考人物时，窗口仍可正常操作')

    def set_running(self,running):
        self.start_button.configure(state='disabled' if running else 'normal')
        self.stop_button.configure(state='normal' if running else 'disabled')
        self.keep_button.configure(state='disabled' if running else 'normal')
        self.exclude_button.configure(state='disabled' if running else 'normal')
        for widget in self.controls:
            widget.configure(state='disabled' if running else 'readonly' if isinstance(widget,ttk.Combobox) else 'normal')

    def stop(self):
        self.controller.stop()
        self.stop_button.configure(state='disabled')
        self.status.set('正在停止，已完成结果和缓存会保留…')

    def filter(self,label):
        self.filter_name=label
        self.render_rows()

    def render_rows(self):
        selected={self.row_map[i]['file'] for i in self.table.selection() if i in self.row_map}
        self.table.delete(*self.table.get_children())
        self.row_map={}
        rows=self.controller.rows
        counts={'已保留':sum(bool(r['copied']) for r in rows),'待确认':sum(r['status']=='review' for r in rows),
            '全部':len(rows),'未匹配':sum(r['status'] in ('no_match','no_person','manual_excluded') for r in rows),
            '异常':sum(r['status']=='error' for r in rows)}
        for label,button in self.filter_buttons.items():
            button.configure(text=f'{label} {counts[label]}',style='Selected.TButton' if label==self.filter_name else 'TButton')
        search=self.search.get().strip().casefold()
        for i,row in enumerate(sorted(rows,key=lambda r:(not r['copied'],-r['score']))):
            include=(self.filter_name=='全部' or self.filter_name=='已保留' and row['copied'] or
                self.filter_name=='待确认' and row['status']=='review' or self.filter_name=='异常' and row['status']=='error' or
                self.filter_name=='未匹配' and row['status'] in ('no_match','no_person','manual_excluded'))
            if not include or search and search not in row['file'].casefold():continue
            iid=str(i)
            self.row_map[iid]=row
            self.table.insert('', 'end',iid=iid,values=(LABELS.get(row['status'],row['status']),row['file'],f'{row["time_seconds"]:.1f} 秒'),tags=(row['status'],))
            if row['file'] in selected:self.table.selection_add(iid)
        self.report_text.set('结果与复核'+(f'  ·  {self.controller.report.name}' if self.controller.report else ''))

    def selected(self,event=None):
        selection=self.table.selection()
        if not selection:return
        row=self.row_map.get(selection[0])
        if not row:return
        key=(str(self.controller.report),row['file'])
        evidence=self.controller.report/row['evidence'] if row.get('evidence') else None
        if key!=self.selected_key:
            self.show_video(Path(row['source_path']),evidence,max(0,row['time_seconds']-.6))
            self.selected_key=key
        color_text=f'匹配依据：上装 {row["upper_color"]:.2f} · 下装 {row["lower_color"]:.2f} · 支持 {row["support_frames"]} 帧'
        self.detail.set(f'{LABELS.get(row["status"],row["status"])}\n{color_text}\n'+(row['error'] if row.get('error') else '可播放完整视频，再决定保留或排除。'))

    def show_video(self,path,evidence=None,position=0):
        self.selected_key=None
        self.player.stop();self.playing=False
        self.play_button.configure(text='播放视频')
        self.preview_source=Path(path)
        self.preview_title.configure(text=self.preview_source.name)
        self.seek.set(position)
        self.slider.configure(to=max(60,position+1))
        self.time_text.set(f'{self.format_time(position)} / --:--')
        self.preview_image=None
        if evidence and Path(evidence).exists():
            with Image.open(evidence) as image:self.preview_image=image.convert('RGB').copy()
        self.render_image()

    def render_image(self):
        canvas=self.canvas
        canvas.delete('all')
        width,height=canvas.winfo_width(),canvas.winfo_height()
        if width<4 or height<4:return
        if self.preview_image is not None:
            fitted=ImageOps.contain(self.preview_image,(width-12,height-12),Image.Resampling.LANCZOS)
            self.preview_photo=ImageTk.PhotoImage(fitted)
            canvas.create_image(width/2,height/2,image=self.preview_photo)
        else:
            canvas.create_text(width/2,height/2,text='选择视频后，点击播放查看完整画面',fill='#c4cedf',font=('Microsoft YaHei UI',11),width=width-40)

    def toggle_play(self):
        if not self.preview_source:return
        if self.playing:
            self.seek.set(self.player.position)
            self.player.stop();self.playing=False
            self.play_button.configure(text='继续播放')
        else:
            self.player.play(self.preview_source,self.seek.get())
            self.playing=True
            self.play_button.configure(text='暂停')

    def seek_video(self):
        if not self.preview_source:return
        self.player.play(self.preview_source,self.seek.get())
        self.playing=True
        self.play_button.configure(text='暂停')

    def release_seek(self,event=None):
        self.scrubbing=False
        self.seek_video()

    @staticmethod
    def format_time(value):
        seconds=max(0,int(value))
        return f'{seconds//60:02d}:{seconds%60:02d}'

    def review(self,decision):
        rows=[self.row_map[i] for i in self.table.selection() if i in self.row_map]
        if not rows:
            messagebox.showinfo('先选择视频','点击列表选择视频，Ctrl / Shift 可以多选。',parent=self.window)
            return
        try:
            done=self.controller.review(rows,decision)
            self.status.set(f'已{ "保留" if decision=="keep" else "排除" } {len(done)} 段视频；本轮复核决定已保存')
            self.render_rows();self.update_counts()
        except Exception as exc:
            self.render_rows()
            messagebox.showerror('操作未完成',str(exc),parent=self.window)

    def choose_report(self):
        if self.controller.running:
            messagebox.showinfo('任务进行中','请先停止或等待筛选完成，再打开历史结果。',parent=self.window);return
        chosen=filedialog.askopenfilename(title='打开历史结果中的 summary.json',initialdir=self.root/'reports',filetypes=[('筛选结果','summary.json')])
        if chosen:
            try:self.controller.load(Path(chosen).parent);self._loaded(update_paths=True)
            except Exception as exc:messagebox.showerror('无法读取报告',str(exc),parent=self.window)

    def _loaded(self,update_paths=False):
        summary=self.controller.summary
        if update_paths:
            for key,field in [('reference','reference_dir'),('source','input_dir'),('output','output_dir')]:
                if summary.get(field):self.paths[key].set(summary[field])
        self.filter('已保留')
        self.progress.configure(maximum=max(1,summary.get('total',len(self.controller.rows))),value=len(self.controller.rows))
        count=sum(r['copied'] for r in self.controller.rows)
        review=sum(r['status']=='review' for r in self.controller.rows)
        self.status.set(f'已载入 {len(self.controller.rows)} 段结果 · 已保留 {count} 段 · 待确认 {review} 段')
        self.progress_text.set('选择视频可预览，并直接保留或排除；新任务使用上方目录')
        children=self.table.get_children()
        if children:self.table.selection_set(children[0]);self.table.focus(children[0]);self.selected()
        self.update_counts()

    def _open(self,path):
        try:
            path=Path(path)
            if not path.exists():raise ValueError('路径不存在：'+str(path))
            os.startfile(str(path))
        except Exception as exc:messagebox.showerror('无法打开',str(exc),parent=self.window)

    def open_output(self):
        path=Path(self.paths['output'].get())
        try:path.mkdir(parents=True,exist_ok=True)
        except OSError as exc:
            messagebox.showerror('无法打开',str(exc),parent=self.window)
            return
        self._open(path)

    def open_report(self):
        if self.controller.report:self._open(self.controller.report/'review.html')

    def open_backup(self):
        candidates=[self.backup,self.controller.report/'removed_from_output' if self.controller.report else None,self.root/'reports/archives',self.root/'reports/previous_output_20261011']
        for path in candidates:
            if path and Path(path).exists():self._open(path);return
        messagebox.showinfo('暂无备份','归档旧输出或排除已保留视频后，会生成备份。',parent=self.window)

    def open_video(self):
        if self.preview_source:self._open(self.preview_source)

    def show_references(self):
        paths=video_files(self.paths['reference'].get())
        if not paths:
            messagebox.showinfo('没有参考视频','请先选择包含目标人物的参考视频目录。',parent=self.window);return
        dialog=tk.Toplevel(self.window)
        dialog.title('参考视频 · 同一目标人物')
        dialog.geometry('590x330')
        ttk.Label(dialog,text='选择一段参考，在主窗口预览完整视频',padding=12).pack(anchor='w')
        listing=tk.Listbox(dialog,font=('Microsoft YaHei UI',11),selectmode='single',borderwidth=0)
        listing.pack(fill='both',expand=True,padx=12)
        for path in paths:listing.insert('end',path.name)
        def preview():
            if not listing.curselection():return
            path=paths[listing.curselection()[0]]
            images=sorted((self.controller.report/'evidence').glob(f'ref_{path.stem}_*.jpg')) if self.controller.report else []
            self.show_video(path,images[len(images)//2] if images else None,0)
            self.detail.set('参考视频：确认其中只有本轮要寻找的目标人物。')
            dialog.destroy()
        listing.bind('<Double-1>',lambda _:preview())
        listing.selection_set(0)
        ttk.Button(dialog,text='预览选中参考',command=preview).pack(pady=10)

    def log(self,message):
        if not message:return
        self.logs.append(message)
        self.logs=self.logs[-1000:]
        self.log_widget.configure(state='normal')
        self.log_widget.insert('end',message+'\n')
        self.log_widget.see('end')
        self.log_widget.configure(state='disabled')

    def _poll(self):
        dirty=False
        for _ in range(100):
            try:event=self.controller.events.get_nowait()
            except queue.Empty:break
            kind=event['type']
            if kind=='log':self.log(event['message'])
            elif kind=='started':self.progress.configure(maximum=max(1,event['total']),value=0)
            elif kind=='phase':
                if not self.closing:self.status.set(event['message'])
            elif kind=='clip_progress':
                self.progress_text.set(f'当前视频已处理 {event["decoded"]} 帧 · 已完成 {len(self.controller.rows)}/{self.controller.summary.get("total",0)} 段')
            elif kind=='device':self.device.set(event['name']+' · 视频不上传')
            elif kind=='backup':
                self.backup=Path(event['path']);self.log(f'旧输出 {event["count"]} 段已归档至 {event["path"]}')
            elif kind=='result':
                self.controller.rows.append(event['row'])
                self.progress.configure(value=event['processed'])
                self.progress_text.set(f'已完成 {event["processed"]}/{event["total"]} 段 · 已保留 {event["copied"]} 段 · 异常 {event["errors"]} 段')
                dirty=True
            elif kind in ('fatal','cancelled'):
                self.status.set(('任务失败：' if kind=='fatal' else '')+event['message'])
                self.log(event['message'])
            elif kind=='finished':
                self.controller.summary=event
                self.status.set(('已停止' if event['cancelled'] else '筛选完成')+f' · 已检查 {event["processed"]} 段 · 已保留 {event["copied"]} 段')
                if event['errors']:self.status.set(self.status.get()+f' · {event["errors"]} 段异常需检查')
                self.log(self.status.get())
            elif kind=='exit':
                self.set_running(False)
                if self.controller.report and (self.controller.report/'results.csv').exists():
                    try:self.controller.load(self.controller.report)
                    except Exception as exc:self.log(str(exc))
                if event['code'] and '失败' not in self.status.get():self.status.set('任务未完全完成，请查看异常结果或运行日志')
                dirty=True;self.update_counts()
                if self.closing:
                    self.player.stop();self.window.destroy();return
        if dirty:self.render_rows()
        for _ in range(20):
            try:event=self.player.events.get_nowait()
            except queue.Empty:break
            if event['generation']!=self.player.generation:continue
            if event['type']=='duration':
                self.slider.configure(to=max(.1,event['duration']))
            elif event['type'] in ('ended','error'):
                self.playing=False;self.play_button.configure(text='重新播放')
                if event['type']=='ended':self.seek.set(0)
                else:self.detail.set('预览失败：'+event['message']);self.log(event['message'])
        try:
            generation,frame,position=self.player.frames.get_nowait()
            if generation==self.player.generation:
                self.preview_image=Image.frombytes('RGB',(WIDTH,HEIGHT),frame)
                if not self.scrubbing:self.seek.set(position)
                self.time_text.set(f'{self.format_time(position)} / {self.format_time(self.player.duration)}')
                self.render_image()
        except queue.Empty:pass
        self.window.after(66,self._poll)

    def close(self):
        self.player.stop()
        if self.controller.running:
            self.closing=True
            self.controller.stop()
            self.set_running(True)
            self.stop_button.configure(state='disabled')
            self.status.set('正在停止任务并保存结果，完成后自动关闭…')
        else:self.window.destroy()


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--smoke-test',action='store_true')
    parser.add_argument('--screenshot',type=Path)
    args=parser.parse_args()
    try:ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError,OSError):pass
    window=tk.Tk()
    app=App(window)
    if args.smoke_test:
        def finish():
            try:
                window.update_idletasks()
                assert app.table.winfo_height()>100
                assert app.canvas.winfo_width()>200
                if args.screenshot:
                    from PIL import ImageGrab
                    window.lift();window.attributes('-topmost',True);window.update()
                    ImageGrab.grab(bbox=(window.winfo_rootx(),window.winfo_rooty(),
                        window.winfo_rootx()+window.winfo_width(),window.winfo_rooty()+window.winfo_height())).save(args.screenshot)
                save_json(app.root/'runtime/gui_smoke.json',{'ok':True,'rows':len(app.controller.rows),'visible_rows':len(app.table.get_children()),
                    'window_size':[window.winfo_width(),window.winfo_height()],'report':str(app.controller.report)})
            finally:app.close()
        window.after(1500,finish)
    window.mainloop()


if __name__=='__main__':
    main()
