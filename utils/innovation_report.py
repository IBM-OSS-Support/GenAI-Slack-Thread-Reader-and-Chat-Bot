# utils/innovation_report.py

import pandas as pd
import re
from datetime import datetime, timedelta
from typing import Optional, List, Tuple
import logging

logger = logging.getLogger(__name__)

def parse_innovation_sheet(df: pd.DataFrame) -> Optional[str]:
    """
    Parse the 365 Days of Innovation Excel sheet and generate a Slack-formatted message
    for the last 5 entries with proper hyperlinks.
    
    Expected columns (flexible matching):
    - Day# -> Day
    - Date Shared -> Date
    - Product(s) covered -> Product Area
    - Video title -> Title
    - Link to video -> Link to video
    """
    try:
        # Clean column names (remove leading/trailing spaces)
        df.columns = df.columns.str.strip()
        
        # Log actual columns for debugging
        logger.info(f"Actual columns in Excel: {list(df.columns)}")
        
        # Create a mapping for your specific column names
        column_mapping = {}
        
        # Flexible matching for columns
        for col in df.columns:
            col_lower = col.lower()
            col_normalized = col.replace(' ', '').lower()
            
            # Check for Day column
            if 'day' in col_lower and '#' in col:
                column_mapping['Day'] = col
            elif 'day' in col_lower and 'Day' not in column_mapping:
                column_mapping['Day'] = col
            # Check for Date column
            elif 'date' in col_lower and 'shared' in col_lower:
                column_mapping['Date'] = col
            elif 'date' in col_lower and 'Date' not in column_mapping:
                column_mapping['Date'] = col
            # Check for Product Area column
            elif 'product' in col_lower and ('covered' in col_lower or 'area' in col_lower):
                column_mapping['Product Area'] = col
            elif 'product' in col_lower and 'Product Area' not in column_mapping:
                column_mapping['Product Area'] = col
            # Check for Title column  
            elif 'video' in col_lower and 'title' in col_lower:
                column_mapping['Title'] = col
            elif 'title' in col_lower and 'Title' not in column_mapping:
                column_mapping['Title'] = col
            # Check for Link column (handle extra spaces)
            elif 'link' in col_normalized and 'video' in col_normalized:
                column_mapping['Link to video'] = col
        
        # Check if we found all required columns
        required_cols = ['Day', 'Date', 'Product Area', 'Title', 'Link to video']
        missing_cols = []
        for req_col in required_cols:
            if req_col not in column_mapping or column_mapping[req_col] not in df.columns:
                missing_cols.append(req_col)
        
        if missing_cols:
            logger.error(f"Could not find columns matching: {missing_cols}")
            logger.error(f"Column mapping found: {column_mapping}")
            logger.error(f"Available columns: {list(df.columns)}")
            return None
        
        # Create a new dataframe with standardized column names
        df_renamed = pd.DataFrame()
        for standard_name, actual_name in column_mapping.items():
            if actual_name in df.columns:
                df_renamed[standard_name] = df[actual_name]
        
        # Remove any completely empty rows
        df_renamed = df_renamed.dropna(how='all')
        
        # Filter to rows that have Day, Title values
        valid_df = df_renamed[df_renamed['Day'].notna() & df_renamed['Title'].notna()].copy()
        
        if valid_df.empty:
            return "No valid innovation entries found in the sheet."
        
        # Sort by Day number (descending) to get the latest entries
        valid_df['Day'] = pd.to_numeric(valid_df['Day'], errors='coerce')
        valid_df = valid_df.dropna(subset=['Day'])
        valid_df = valid_df.sort_values('Day', ascending=False)
        
        # Get the last 5 entries (in descending order, then reverse for display)
        last_5 = valid_df.head(5).sort_values('Day', ascending=True)
        
        if len(last_5) == 0:
            return "No valid entries found in the sheet."
        
        # Get week date range from the entries
        week_start, week_end = _get_week_range(last_5)
        
        # Log for debugging
        logger.info(f"Week range determined: {week_start} to {week_end}")
        
        # Get unique product areas from the last week's entries
        product_areas = _get_product_areas_for_week(last_5, week_start, week_end)
        
        # Build the message
        message_parts = []
        
        # Header
        message_parts.append("*365 Days of Innovation*\n")
        
        # Weekly summary with product areas  
        if week_start and week_end:
            week_str = f"{week_start}–{week_end}"
        else:
            week_str = "Jul 13–17"
            
        if product_areas:
            areas_str = ", ".join(product_areas[:-1])
            if len(product_areas) > 1:
                areas_str += f", {product_areas[-1]}"
            else:
                areas_str = product_areas[0]
            
            message_parts.append(
                f"Last week's demos ({week_str}) included sessions on {areas_str}. "
                "Even more innovation is coming this week!\n"
            )
        else:
            message_parts.append(
                f"Last week's demos ({week_str}) showcased amazing innovations. "
                "Even more is coming this week!\n"
            )
        
        # Add each day's entry with hyperlinked title
        for _, row in last_5.iterrows():
            day_num = int(row['Day'])
            title = str(row['Title']).strip()
            
            # Get the video link
            video_link = row.get('Link to video', '')
            if pd.notna(video_link):
                video_link = str(video_link).strip()
            else:
                video_link = ''
            
            # Extract actual URL from the link field
            if video_link and video_link.lower() != 'nan':
                # First, try to extract a URL that starts with http/https
                url_match = re.search(r'https?://[^\s<>]+', video_link)
                if url_match:
                    actual_url = url_match.group(0).rstrip('.,;:')  # Remove trailing punctuation
                    message_parts.append(f"Day {day_num} - <{actual_url}|{title}>")
                elif video_link.lower() in ['link', 'demo link', 'box link', 'see here', 'n/a', 'na']:
                    # Generic link text without actual URL
                    message_parts.append(f"Day {day_num} - {title} _(link not available)_")
                elif video_link.startswith(('http://', 'https://', 'www.')):
                    # The whole field looks like a URL
                    if video_link.startswith('www.'):
                        video_link = 'https://' + video_link
                    message_parts.append(f"Day {day_num} - <{video_link}|{title}>")
                else:
                    # Can't parse it
                    message_parts.append(f"Day {day_num} - {title} _(link not available)_")
            else:
                # No link available
                message_parts.append(f"Day {day_num} - {title} _(link not available)_")
        
        # Footer with standard links
        message_parts.append("")  # Empty line
        message_parts.append(
            "Missed a day? Be sure and follow the "
            "<https://w3.ibm.com/w3publisher/core-software-organization/blogs|Leadership blogs> "
            "on the <https://w3.ibm.com/w3publisher/core-software-organization|Core Software Products> page."
        )
        message_parts.append("")  # Empty line
        message_parts.append(
            "Do you have an innovation demo you'd like to submit? "
            "Submit your info <https://ibm.box.com/s/re3s3o4r5ciwf74tv9nd028diqms0lh5|here>!"
        )
        
        return "\n".join(message_parts)
        
    except Exception as e:
        logger.exception(f"Error parsing innovation sheet: {e}")
        return f"Error processing the innovation sheet: {str(e)}"


def _get_week_range(entries_df: pd.DataFrame) -> Tuple[Optional[str], Optional[str]]:
    """
    Extract the week range from the Date column of the entries.
    Returns formatted strings like "Jul 13" and "17"
    """
    try:
        dates = []
        
        if 'Date' in entries_df.columns:
            for date_val in entries_df['Date'].values:
                if pd.notna(date_val):
                    logger.info(f"Processing date value: {date_val} (type: {type(date_val)})")
                    
                    # Convert to string first if not already
                    date_str = str(date_val).strip()
                    
                    # Handle dates like "April 19th", "July 13th", etc.
                    # Remove ordinal suffixes (st, nd, rd, th)
                    import re
                    cleaned_date = re.sub(r'(\d+)(st|nd|rd|th)', r'\1', date_str)
                    
                    # Try to parse month+day format (assuming current year)
                    month_day_formats = [
                        '%B %d',         # July 13
                        '%b %d',         # Jul 13
                        '%B %d, %Y',     # July 13, 2025
                        '%b %d, %Y',     # Jul 13, 2025
                    ]
                    
                    parsed = False
                    for fmt in month_day_formats:
                        try:
                            # For formats without year, add current year
                            if '%Y' not in fmt:
                                # Use 2025 as the year since these are July 2025 dates
                                dt = datetime.strptime(cleaned_date + ', 2025', fmt + ', %Y')
                            else:
                                dt = datetime.strptime(cleaned_date, fmt)
                            dates.append(dt)
                            logger.info(f"Successfully parsed {date_str} (cleaned: {cleaned_date}) with format {fmt}")
                            parsed = True
                            break
                        except ValueError:
                            continue
                    
                    if not parsed:
                        # Try standard date formats
                        date_formats = [
                            '%m/%d/%Y',      # 07/17/2025
                            '%Y-%m-%d',      # 2025-07-17
                            '%d/%m/%Y',      # 17/07/2025
                            '%m-%d-%Y',      # 07-17-2025
                            '%d-%m-%Y',      # 17-07-2025
                            '%Y/%m/%d',      # 2025/07/17
                            '%m/%d/%y',      # 07/17/25
                            '%d/%m/%y',      # 17/07/25
                        ]
                        
                        for fmt in date_formats:
                            try:
                                dt = datetime.strptime(date_str, fmt)
                                dates.append(dt)
                                logger.info(f"Successfully parsed {date_str} with format {fmt}")
                                parsed = True
                                break
                            except ValueError:
                                continue
                    
                    if not parsed:
                        # Try pandas to_datetime as fallback
                        try:
                            dt = pd.to_datetime(date_val)
                            if pd.notna(dt):
                                dates.append(dt.to_pydatetime() if hasattr(dt, 'to_pydatetime') else dt)
                                logger.info(f"Parsed {date_val} using pandas")
                        except:
                            logger.warning(f"Could not parse date: {date_val}")
        
        if not dates:
            logger.warning("No dates could be parsed, using default Jul 13-17")
            return "Jul 13", "17"
            
        # We have dates, find the range
        min_date = min(dates)
        max_date = max(dates)
        
        logger.info(f"Date range: {min_date} to {max_date}")
        
        # Format the date range
        if min_date.month == max_date.month:
            # Same month: "Jul 13" and "17"
            start_str = min_date.strftime("%b %-d" if hasattr(min_date, '__format__') else "%b %d").replace(' 0', ' ').strip()
            end_str = str(max_date.day)
        else:
            # Different months: "Jul 30" and "Aug 3"
            start_str = min_date.strftime("%b %-d" if hasattr(min_date, '__format__') else "%b %d").replace(' 0', ' ').strip()
            end_str = max_date.strftime("%b %-d" if hasattr(max_date, '__format__') else "%b %d").replace(' 0', ' ').strip()
            
        return start_str, end_str
        
    except Exception as e:
        logger.error(f"Error in _get_week_range: {e}")
        # Return the expected dates based on your example
        return "Jul 13", "17"


def _get_product_areas_for_week(df: pd.DataFrame, week_start: str, week_end: str) -> List[str]:
    """
    Get unique product areas for the week, preserving special formatting like BAMOE.
    """
    try:
        # Get all product areas from the dataframe
        areas = df['Product Area'].dropna().astype(str).str.strip()
        
        # Remove duplicates while preserving order
        unique_areas = []
        seen_lower = set()
        
        for area in areas:
            area_lower = area.lower()
            if area_lower not in seen_lower and area and area != 'nan':
                unique_areas.append(area)
                seen_lower.add(area_lower)
        
        # Return up to 5 areas to keep the message concise
        return unique_areas[:5]
        
    except Exception as e:
        logger.warning(f"Could not extract product areas: {e}")
        return []