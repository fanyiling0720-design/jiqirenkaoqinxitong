"""Code-drawn robot identity; no external fonts or image service required."""
import tkinter as tk


def robot_mark(parent, size=64, background='#14243B'):
    canvas = tk.Canvas(parent, width=size, height=size, bg=background,
                       highlightthickness=0, bd=0)
    scale = size / 64
    def rect(box, fill, radius=8):
        x1,y1,x2,y2 = [n*scale for n in box]
        r = radius*scale
        canvas.create_polygon(x1+r,y1,x2-r,y1,x2,y1,x2,y1+r,
            x2,y2-r,x2,y2,x2-r,y2,x1+r,y2,x1,y2,x1,y2-r,
            x1,y1+r,x1,y1, smooth=True, splinesteps=24, fill=fill, outline='')
    rect((1,1,63,63),'#4165E8',14)
    canvas.create_line(32*scale,13*scale,32*scale,21*scale,fill='#B8DFFF',width=2*scale)
    canvas.create_oval(29*scale,10*scale,35*scale,16*scale,fill='#69E2CB',outline='')
    rect((11,29,16,40),'#B8DFFF',2)
    rect((48,29,53,40),'#B8DFFF',2)
    rect((16,21,48,49),'#FFFFFF',7)
    rect((21,27,43,39),'#142C50',4)
    for x in (26,38):
        canvas.create_oval((x-2)*scale,30*scale,(x+2)*scale,35*scale,fill='#69E2CB',outline='')
    canvas.create_line(27*scale,44*scale,37*scale,44*scale,fill='#4165E8',width=2*scale,capstyle='round')
    return canvas


def nav_mark(parent, key, background, foreground='#93AAC7'):
    c = tk.Canvas(parent,width=22,height=24,bg=background,bd=0,highlightthickness=0)
    opts = dict(fill=foreground,width=1.7,capstyle='round',joinstyle='round')
    if key=='home':
        c.create_line(3,11,11,4,19,11,**opts)
        c.create_line(5,10,5,20,17,20,17,10,**opts)
        c.create_line(9,20,9,14,13,14,13,20,**opts)
    elif key=='history':
        c.create_oval(3,4,19,20,outline=foreground,width=1.7)
        c.create_line(11,8,11,12,15,14,**opts)
    elif key=='students':
        c.create_oval(7,3,15,11,outline=foreground,width=1.7)
        c.create_arc(3,11,19,25,start=0,extent=180,style='arc',outline=foreground,width=1.7)
    elif key=='today':
        c.create_rectangle(4,5,18,21,outline=foreground,width=1.7)
        c.create_line(8,3,14,3,**opts)
        c.create_line(7,13,10,16,15,10,**opts)
    elif key=='courses':
        c.create_rectangle(3,6,19,21,outline=foreground,width=1.7)
        c.create_line(3,11,19,11,**opts)
        c.create_line(7,3,7,8,**opts)
        c.create_line(15,3,15,8,**opts)
    else:
        c.create_oval(4,5,18,19,outline=foreground,width=1.7)
        c.create_oval(8,9,14,15,outline=foreground,width=1.7)
        for a,b,d,e in [(11,2,11,5),(11,19,11,22),(1,12,4,12),(18,12,21,12)]:
            c.create_line(a,b,d,e,**opts)
    return c
