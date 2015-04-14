#!/usr/bin/python
from __future__ import print_function

__author__ = 'jjolly'

import argparse
import os
import py7zlib
import zipfile
from sys import stdin, stderr
from xml.dom import minidom
try:
    import xml.etree.cElementTree as ET
except ImportError:
    import xml.etree.ElementTree as ET


def debug_print(*objs):
    print("DEBUG: ", *objs, file=stderr)


# Helper function to validate the rompath argument
def list_dir(path):
    try:
        filelist = os.listdir(path)
    except OSError as err:
        msg = "%r is not a valid rompath, %r" % (path, err.strerror)
        raise argparse.ArgumentTypeError(msg)
    return [os.path.join(path, name) for name in filelist if name[-3:] == '.7z' or name[-4:] == '.zip']


# Read a standard DAT file from an open file and produce a dictionary of games, their roms and attributes
def parse_dat(datfile):
    datroot = ET.ElementTree(file=datfile).getroot()

    games = {}

    for child in datroot:
        if child.tag == 'game':
            gamename = child.attrib['name']
            games[gamename] = {'roms': {}}
            if 'cloneof' in child.attrib:
                games[gamename]['cloneof'] = child.attrib['cloneof']
            elif 'romof' in child.attrib:
                games[gamename]['romof'] = child.attrib['romof']

            if 'cloneof' in child.attrib and 'romof' in child.attrib \
                    and child.attrib['cloneof'] != child.attrib['romof']:
                debug_print("Well, that's odd. Game " + gamename + " has cloneof:" + child.attrib['cloneof'] +
                            " and romof:" + child.attrib['romof'])

            for rom in child:
                if rom.tag == 'description' or rom.tag == 'year':
                    games[gamename][rom.tag] = rom.text
                if rom.tag == 'rom':
                    romname = rom.attrib['name']
                    romsize = int(rom.attrib['size'])
                    romcrc = 0
                    if 'status' not in rom.attrib or rom.attrib['status'] != 'nodump':
                        romcrc = int(rom.attrib['crc'], base=16)
                        if romcrc == 0:
                            debug_print("Well, this is a problem. Game " + gamename + ", rom " + romname +
                                        " has a crc of zero but is not NODUMP")
                    hash = (romsize, romcrc)
                    games[gamename]['roms'][romname] = {'hash': hash}
                    if 'merge' in rom.attrib:
                        if 'cloneof' not in child.attrib and 'romof' not in child.attrib:
                            debug_print("Game " + gamename + " has a merge tag in rom " + romname +
                                        " (" + rom.attrib['merge'] + ") but no cloneof or romof")
                        games[gamename]['roms'][romname]['merge'] = rom.attrib['merge']

    return games


def read_zip_dir(rompaths, roms, files):
    for zippath in rompaths:
        zipname = os.path.basename(os.path.splitext(zippath)[0])
        files[zipname] = {}
        try:
            archive = py7zlib.Archive7z(open(zippath))
            for romname in archive.getnames():
                rom = archive.getmember(romname)
                hash = (rom.size, rom.digest)
                files[zipname][romname] = hash
                if hash not in roms:
                    roms[hash] = {'name': zipname, 'path': zippath, 'file': romname}
        except py7zlib.FormatError:
            try:
                archive = zipfile.ZipFile(zippath)
                for rominfo in archive.infolist():
                    hash = (rominfo.file_size, rominfo.CRC)
                    files[zipname][rominfo.filename] = hash
                    if hash not in roms:
                        roms[hash] = {'name': zipname, 'path': zippath, 'file': rominfo.filename}
            except:
                pass

    return roms, files


def read_zips(rompaths, addpaths):
    roms, files = read_zip_dir(rompaths, {}, {})
    roms, _ = read_zip_dir(addpaths, roms, {})

    return roms, files


def find_missing(games, files, roms):
    missing = ET.Element('missing')

    for (gamename, gamedata) in games.items():
        for (romname, romdata) in gamedata['roms'].items():
            # Skip NODUMP entries
            if romdata['hash'][1] == 0:
                continue

            endgame = gamename
            endrom = romname
            endname = endrom
            sizemismatch = False

            # Find the topmost rom
            while 'cloneof' in games[endgame]:
                if 'merge' in games[endgame]['roms'][endrom]:
                    endrom = games[endgame]['roms'][endrom]['merge']
                    endname = endrom
                    endgame = games[endgame]['cloneof']
                elif not args.unmerged:
                    endname = os.path.join(gamename, endrom)
                    endgame = games[endgame]['cloneof']
                else:
                    break

            if 'romof' in games[endgame]:
                if endname == endrom and \
                        (endrom not in games[endgame]['roms'] or 'merge' in games[endgame]['roms'][endrom]):
                    if endrom in games[endgame]['roms']:
                        endrom = games[endgame]['roms'][endrom]['merge']
                        endname = endrom
                    endgame = games[endgame]['romof']

            if endgame in files:
                if endname in files[endgame]:
                    if files[endgame][endname] == romdata['hash']:
                        continue
                    elif files[endgame][endname][1] == romdata['hash'][1]:
                        sizemismatch = True

            game = missing.find("game[@name='" + gamename + "']")
            if not game:
                game = ET.SubElement(missing, 'game')
                game.set('name', gamename)
            rom = game.find("rom[@name='" + romname + "']")
            if not rom:
                rom = ET.SubElement(game, 'rom')
                rom.set('name', romname)
            if sizemismatch:
                ET.SubElement(rom, 'size_mismatch')
            if romdata['hash'] in roms:
                found = ET.SubElement(rom, 'found')
                found.set('path', roms[romdata['hash']]['path'])
                found.set('file', roms[romdata['hash']]['file'])

    return ET.ElementTree(element=missing)

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument("rompath", help="Path to romfiles to scan", type=list_dir)
    parser.add_argument("-a", "--addpath", help="Path to add to rom search",
                        type=list_dir, action='append', default=[])
    parser.add_argument("-d", "--datpath", help="Path to datfile to read",
                        default=stdin, type=argparse.FileType('r'))
    parser.add_argument("-u", "--unmerged", help="Set to scan is unmerged", action='store_true')
    parser.add_argument("-v", "--verbose", help="Verbose logging to stderr", action='store_true')
    args = parser.parse_args()

    addpath = [j for i in args.addpath for j in i]

    games = parse_dat(args.datpath)

    if args.verbose:
        debug_print("Parsing DAT complete")

    roms, files = read_zips(args.rompath, addpath)

    if args.verbose:
        debug_print("Reading romfiles complete")

    missing_tree = find_missing(games, files, roms)

    print(minidom.parseString(ET.tostring(missing_tree.getroot())).toprettyxml(indent="  "))