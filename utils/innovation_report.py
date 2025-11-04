# utils/innovation_report.py

import pandas as pd
import re
from datetime import datetime, timedelta
from typing import Optional, List, Tuple
import logging

logger = logging.getLogger(__name__)

def parse_innovation_sheet(df: pd.DataFrame, day_range: Optional[Tuple[int, int]] = None) -> Optional[str]:
    """
    Parse the 365 Days of Innovation Excel sheet and generate a Slack-formatted message
    for specified day range or last 5 entries if no range provided.
    
    Args:
        df: The Excel dataframe
        day_range: Optional tuple of (start_day, end_day) to filter entries
    
    Expected columns (flexible matching):
    - Day# -> Day
    - Date Shared -> Date
    - Product(s) covered -> Product Area
    - Video title -> Title
    - Link to video -> Link to video (not used for hyperlinks)
    - Link -> Blog Link (used for hyperlinks)
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
            # Check for Link to video column (not used but mapped for completeness)
            elif 'link' in col_lower and 'video' in col_lower:
                column_mapping['Link to video'] = col
            # Check for Blog Link column - EXACT match for "Link"
            elif col == 'Link':
                column_mapping['Blog Link'] = col
        
        # Check if we found all required columns (Blog Link is what we need for hyperlinks)
        required_cols = ['Day', 'Date', 'Product Area', 'Title', 'Blog Link']
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
        
        # Convert Day column to numeric
        valid_df['Day'] = pd.to_numeric(valid_df['Day'], errors='coerce')
        valid_df = valid_df.dropna(subset=['Day'])
        
        # Filter based on day range or get last 5
        if day_range:
            start_day, end_day = day_range
            logger.info(f"Filtering for day range: {start_day} to {end_day}")
            
            # Filter entries within the specified day range
            filtered_df = valid_df[(valid_df['Day'] >= start_day) & (valid_df['Day'] <= end_day)]
            
            if filtered_df.empty:
                return f"No innovation entries found for days {start_day} to {end_day}."
            
            # Sort by Day number ascending for display
            entries_to_show = filtered_df.sort_values('Day', ascending=True)
            
            logger.info(f"Found {len(entries_to_show)} entries in range {start_day}-{end_day}")
        else:
            # Default behavior: get last 5 entries
            valid_df = valid_df.sort_values('Day', ascending=False)
            entries_to_show = valid_df.head(5).sort_values('Day', ascending=True)
            logger.info(f"Using last 5 entries (default behavior)")
        
        if len(entries_to_show) == 0:
            return "No valid entries found in the specified range."
        
        # Get week date range from the entries
        week_start, week_end = _get_week_range(entries_to_show)
        
        # Log for debugging
        logger.info(f"Week range determined: {week_start} to {week_end}")
        
        # Get unique product areas from the entries
        product_areas = _get_product_areas_for_week(entries_to_show, week_start, week_end)
        
        # Build the message
        message_parts = []
        
        # Header with emotes
        message_parts.append(":culture-innovation: *365 Days of Innovation* :culture-innovation:\n")
        
        # Summary section - always use date range from actual dates
        if week_start and week_end:
            # Format the date range appropriately
            if week_start and week_end and week_start != week_end:
                date_str = f"{week_start}–{week_end}"
            elif week_start:
                date_str = week_start
            else:
                date_str = "this period"
        else:
            date_str = "this period"
        
        if product_areas:
            areas_str = ", ".join(product_areas[:-1])
            if len(product_areas) > 1:
                areas_str += f", and {product_areas[-1]}"
            else:
                areas_str = product_areas[0]
            
            # Use appropriate intro text based on whether range was specified
            if day_range:
                message_parts.append(
                    f"Last week's demos ({date_str}) included sessions on {areas_str}. "
                    "Even more innovation is coming this week!\n"
                )
            else:
                message_parts.append(
                    f"Last week's demos ({date_str}) included sessions on {areas_str}. "
                    "Even more innovation is coming this week!\n"
                )
        else:
            if day_range:
                message_parts.append(
                    f"Last week's demos ({date_str}):\n"
                )
            else:
                message_parts.append(
                    f"Last week's demos ({date_str}) showcased amazing innovations. "
                    "Even more is coming this week!\n"
                )
        
        # Add each day's entry with hyperlinked title using BLOG LINK
        for _, row in entries_to_show.iterrows():
            day_num = int(row['Day'])
            title = str(row['Title']).strip()
            
            # Get the BLOG link from the "Link" column (mapped as "Blog Link")
            blog_link = row.get('Blog Link', '')
            if pd.notna(blog_link):
                blog_link = str(blog_link).strip()
            else:
                blog_link = ''
            
            # Extract actual URL from the blog link field
            if blog_link and blog_link.lower() != 'nan':
                # First, try to extract a URL that starts with http/https
                url_match = re.search(r'https?://[^\s<>]+', blog_link)
                if url_match:
                    actual_url = url_match.group(0).rstrip('.,;:')  # Remove trailing punctuation
                    message_parts.append(f"Day {day_num} - <{actual_url}|{title}>")
                elif blog_link.lower() in ['link', 'demo link', 'box link', 'see here', 'n/a', 'na']:
                    # Generic link text without actual URL
                    message_parts.append(f"Day {day_num} - {title} _(link not available)_")
                elif blog_link.startswith(('http://', 'https://', 'www.')):
                    # The whole field looks like a URL
                    if blog_link.startswith('www.'):
                        blog_link = 'https://' + blog_link
                    message_parts.append(f"Day {day_num} - <{blog_link}|{title}>")
                else:
                    # Can't parse it
                    message_parts.append(f"Day {day_num} - {title} _(link not available)_")
            else:
                # No link available
                message_parts.append(f"Day {day_num} - {title} _(link not available)_")
        
        # Footer with standard links
        message_parts.append(
            "Missed a day? Be sure and follow the "
            "<https://w3.ibm.com/w3publisher/core-software-organization/blogs|Leadership blogs> "
            "on the <https://w3.ibm.com/w3publisher/core-software-organization|Core Software Products> page."
        )
        message_parts.append(
            ":demo3: Do you have an innovation demo you'd like to submit? "
            "Submit your info <https://ibm.box.com/s/re3s3o4r5ciwf74tv9nd028diqms0lh5|here>!"
        )
        
        return "\n".join(message_parts)
        
    except Exception as e:
        logger.exception(f"Error parsing innovation sheet: {e}")
        return f"Error processing the innovation sheet: {str(e)}"


def _get_week_range(entries_df: pd.DataFrame) -> Tuple[Optional[str], Optional[str]]:
    """
    Extract the week range from the Date column of the entries.
    Returns formatted strings like "Sept 16" and "Sept 18" or "Sept 16" and "Oct 2"
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
            logger.warning("No dates could be parsed")
            return None, None
            
        # We have dates, find the range
        min_date = min(dates)
        max_date = max(dates)
        
        logger.info(f"Date range: {min_date} to {max_date}")
        
        # Format the date range with full month abbreviation and day
        if min_date == max_date:
            # Single day
            start_str = min_date.strftime("%b %d").replace(' 0', ' ').strip()
            return start_str, start_str
        elif min_date.month == max_date.month and min_date.year == max_date.year:
            # Same month and year: "Sept 16" and "18"
            start_str = min_date.strftime("%b %d").replace(' 0', ' ').strip()
            end_str = str(max_date.day)
        else:
            # Different months or years: "Sept 30" and "Oct 3"
            start_str = min_date.strftime("%b %d").replace(' 0', ' ').strip()
            end_str = max_date.strftime("%b %d").replace(' 0', ' ').strip()
            
        return start_str, end_str
        
    except Exception as e:
        logger.error(f"Error in _get_week_range: {e}")
        return None, None


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